from __future__ import annotations

import inspect
import logging
import typing
from datetime import datetime
from functools import cmp_to_key
from inspect import Parameter

from bson import ObjectId
from fastapi import FastAPI, Request, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field, create_model
from starlette.responses import JSONResponse

from eidos.agent.agent import Agent, AgentState, ProcessContext, EidolonHandler
from .agent_contract import SyncStateResponse, AsyncStateResponse
from eidos import agent_os


class AgentController:
    name: str
    agent: Agent

    def __init__(self, name: str, agent: Agent):
        self.name = name
        self.agent = agent

    async def start(self, app: FastAPI):
        for handler in sorted(
                self.agent.action_handlers.values().__reversed__(),
                key=cmp_to_key(lambda x, y: -1 if x.is_initializer() else 1 if y.is_initializer() else 0)
        ):
            path = f"/agents/{self.name}"
            if handler.is_initializer():
                path += f"/programs/{handler.name}"
            else:
                path += f"/processes/{{process_id}}/actions/{handler.name}"
            endpoint = self.process_action(handler)
            app.add_api_route(path, endpoint=endpoint, methods=["POST"], tags=[self.name], responses={
                202: {"model": AsyncStateResponse},
                200: {'model': self.create_response_model(handler.name)},
            }, description=handler.description)

        app.add_api_route(
            f"/agents/{self.name}/processes/{{process_id}}/status",
            endpoint=self.get_process_info,
            methods=["GET"],
            response_model=SyncStateResponse,
            tags=[self.name],
        )

    def stop(self, app: FastAPI):
        pass

    async def restart(self, app: FastAPI):
        self.stop(app)
        await self.start(app)

    # todo, defining this on agents and then handling the dynamic function call will give flexibility to define request body
    def process_action(self, handler: EidolonHandler):
        action = handler.name
        async def run_program(
                request: Request,
                body: BaseModel,
                background_tasks: BackgroundTasks,
                process_id: typing.Optional[str] = None,
        ):
            callback = request.headers.get('callback-url')
            execution_mode = request.headers.get('execution-mode', 'async' if callback else 'sync').lower()

            if not process_id:
                process_id = str(ObjectId())
                state = 'UNINITIALIZED'
            else:
                found = await self.get_latest_process_event(process_id)
                if not found:
                    raise HTTPException(status_code=404, detail="Process not found")
                state = found['state']

            handler = self.agent.action_handlers[action]
            if state not in handler.allowed_states:
                raise HTTPException(status_code=409, detail=f"Action \"{action}\" cannot process state \"{state}\"")

            state = dict(process_id=process_id, state="processing", data=dict(action=action, body=body.model_dump()),
                         date=str(datetime.now().isoformat()))
            await agent_os.symbolic_memory.insert_one('processes', state)

            async def run_and_store_response():
                try:
                    # todo -- probably should be **dict(body) per https://docs.pydantic.dev/latest/concepts/serialization/
                    response = await handler.fn(self.agent, **body.__dict__)
                    if isinstance(response, AgentState):
                        state = response.name
                        data = response.data.model_dump() if isinstance(response.data, BaseModel) else response.data
                    else:
                        state = 'terminated'
                        data = response.model_dump() if isinstance(response, BaseModel) else response
                    doc = dict(process_id=process_id, state=state, data=data, date=str(datetime.now().isoformat()))
                except HTTPException as e:
                    doc = dict(
                        process_id=process_id,
                        state="http_error",
                        data=dict(detail=e.detail, status_code=e.status_code),
                        date=str(datetime.now().isoformat())
                    )
                    if e.status_code >= 500:
                        logging.exception(f"Unhandled error raised by handler")
                    else:
                        logging.debug(f"Handler {handler.name} raised a http error", exc_info=True)
                except Exception as e:
                    doc = dict(
                        process_id=process_id,
                        state="unhandled_error",
                        data=dict(error=str(e)),
                        date=str(datetime.now().isoformat())
                    )
                    logging.exception(f"Unhandled error raised by handler")
                await agent_os.symbolic_memory.insert_one('processes', doc)
                if callback:
                    raise Exception("Not implemented")
                return doc

            self.agent.process_context.set(ProcessContext(process_id=process_id, callback_url=callback))
            self.agent.cpu.process_id.set(process_id)
            if execution_mode == 'sync':
                state = await run_and_store_response()
                return self.doc_to_response(state)
            else:
                background_tasks.add_task(run_and_store_response)
                return JSONResponse(AsyncStateResponse(process_id=process_id).model_dump(), 202)

        logging.getLogger("eidolon").info(f"Registering action {action} for program {self.name}")
        sig = inspect.signature(run_program)
        params = dict(sig.parameters)
        params['body'] = params['body'].replace(annotation=(self.agent.get_input_model(action)))
        if handler.is_initializer():
            del params['process_id']
        else:
            replace: Parameter = params['process_id'].replace(annotation=str)
            params['process_id'] = replace
        run_program.__signature__ = sig.replace(parameters=params.values())
        return run_program

    async def get_process_info(self, process_id: str):
        latest_record = await self.get_latest_process_event(process_id)
        return self.doc_to_response(latest_record)

    def doc_to_response(self, latest_record: dict):
        if not latest_record:
            return JSONResponse(dict(detail="Process not found"), 404)
        elif latest_record['state'] == 'unhandled_error':
            return JSONResponse(latest_record['data'], 500)
        elif latest_record['state'] == 'http_error':
            return JSONResponse(dict(detail=latest_record['data']['detail']), latest_record['data']['status_code'])
        else:
            try:
                del latest_record['_id']
            except KeyError:
                pass
            return JSONResponse(SyncStateResponse(
                process_id=latest_record['process_id'],
                state=latest_record['state'],
                data=latest_record['data'],
                available_actions=[
                    action for action, handler in self.agent.action_handlers.items() if latest_record['state'] in handler.allowed_states
                ],
            ).model_dump(), 200)

    async def get_latest_process_event(self, process_id):
        # todo, memory needs to include sorting
        latest_record = None
        records = agent_os.symbolic_memory.find('processes', dict(process_id=process_id))
        async for record in records:
            if not latest_record or record['date'] > latest_record['date']:
                latest_record = record
        return latest_record

    def create_response_model(self, action: str):
        # if we want, we can calculate the literal state and allowed actions statically for most actions. Not for now though.
        fields = {key: (fieldinfo.annotation, fieldinfo) for key, fieldinfo in SyncStateResponse.model_fields.items()}
        return_type = self.agent.get_response_model(action)
        if inspect.isclass(return_type) and issubclass(return_type, AgentState):
            return_type = return_type.model_fields['data'].annotation
        fields['data'] = (return_type, Field(..., description=fields['data'][1].description))

        return create_model(f'{action.capitalize()}ResponseModel', **fields)