from __future__ import annotations

import contextlib
from typing import Annotated, Type

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from starlette.requests import Request

from eidolon_sdk.agent import CodeAgent, register, Agent
from eidolon_sdk.agent_machine import AgentMachine
from eidolon_sdk.agent_os import AgentOS
from eidolon_sdk.agent_program import AgentProgram

app = FastAPI()
client = TestClient(app)


@contextlib.contextmanager
def os_manager(*agents: Type[Agent]):
    programs = [AgentProgram(
        name=agent.__name__.lower(),
        implementation="tests.test_agent_os." + agent.__qualname__
    ) for agent in agents]
    machine = AgentMachine(agent_memory={}, agent_io={}, agent_programs=programs)
    os = AgentOS(machine=machine, machine_yaml="")
    os.start(app)
    try:
        yield
    finally:
        os.stop()


class HelloWorldResponse(BaseModel):
    question: str
    answer: str


class HelloWorld(CodeAgent):
    counter = 0  # todo, this is a hack to make sure function is called. Should wrap with mock instead

    def __init__(self, agent_program: AgentProgram):
        super().__init__(agent_program)
        HelloWorld.counter = 0

    @register(state="idle", transition_to=['idle', 'terminated'])
    async def idle(self, question: Annotated[str, Field(description="The question to ask. Can be anything, but it better be hello")]):
        HelloWorld.counter += 1
        if question == "hello":
            return HelloWorldResponse(question=question, answer="world")
        else:
            raise HTTPException(status_code=500, detail="huge system error handling unprecedented edge case")


class ParamTester(CodeAgent):
    last_call = None

    @register(state="idle")
    async def foo(self, x: int, y: int = 5, z: Annotated[int, Field(description="z is a param")] = 10):
        ParamTester.last_call = (x, y, z)
        return dict(x=x, y=y, z=z)


def test_empty_start():
    with os_manager():
        docs = client.get("/docs")
        assert docs.status_code == 200


def test_program():
    with os_manager(HelloWorld):
        response = client.post("/helloworld", json=dict(question="hello"))
        assert response.status_code == 202


def test_requests_to_running_process():
    with os_manager(HelloWorld):
        pid = client.post("/helloworld", json=dict(question="hello")).json()['process_id']
        response = client.post(f"/helloworld/{pid}/idle", json=dict(question="hello"))
        assert response.status_code == 202


def test_program_actually_calls_code():
    with os_manager(HelloWorld):
        client.post("/helloworld", json=dict(question="hello"))
        assert HelloWorld.counter == 1


def test_non_annotated_params():
    with os_manager(ParamTester):
        response = client.post("/paramtester", json=dict(x=1, y=2, z=3))
        assert response.status_code == 202
        assert ParamTester.last_call == (1, 2, 3)


def test_defaults():
    with os_manager(ParamTester):
        response = client.post("/paramtester", json=dict(x=1))
        assert response.status_code == 202
        assert ParamTester.last_call == (1, 5, 10)


def test_required_param_missing():
    with os_manager(ParamTester):
        response = client.post("/paramtester", json=dict())
        assert response.status_code == 422


def test_program_error():
    with os_manager(HelloWorld):
        response = client.post("/helloworld", json=dict(question="hola"))
        assert response.status_code == 500
