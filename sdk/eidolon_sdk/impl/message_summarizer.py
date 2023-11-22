from typing import List

from jinja2 import Environment, StrictUndefined
from pydantic import Field, BaseModel

from eidolon_sdk.agent_memory import AgentMemory
from eidolon_sdk.cpu.agent_bus import CallContext
from eidolon_sdk.cpu.llm_message import LLMMessage, SystemMessage
from eidolon_sdk.cpu.llm_unit import LLMUnit, LLMUnitConfig
from eidolon_sdk.reference_model import Specable

PROMPT = """
Your job is to summarize a history of previous messages in a conversation between an AI persona and a human.
The conversation you are given is a from a fixed context window and may not be complete.
Messages sent by the AI are marked with the 'assistant' role.
The AI 'assistant' can also make calls to functions, whose outputs can be seen in messages with the 'function' role.
Things the AI says in the message content are considered inner monologue and are not seen by the user.
The only AI messages seen by the user are from when the AI uses 'send_message'.
Messages the user sends are in the 'user' role.
The 'user' role is also used for important system events, such as login events and heartbeat events (heartbeats run the AI's program without user action, allowing the AI to act without prompting from the user sending them a message).
Summarize what happened in the conversation from the perspective of the AI (use the first person).
Keep your summary less than {{WORD_LIMIT}} words, do NOT exceed this word limit, but be through in your response, just under {{WORD_LIMIT}} words.
Only output the summary, do NOT include anything else in your output.

The messages to summarize are:
{{messages}}
"""


class MessageSummary(BaseModel):
    summary: str = Field(description="The summary of the messages")


class MessageSummarizerConfig(BaseModel):
    summary_word_limit: int = Field(default=100, description="The word limit for the summary")


class MessageSummarizer(Specable[MessageSummarizerConfig]):
    llm_config: LLMUnitConfig = None

    def __init__(self, agent_memory: AgentMemory, spec: MessageSummarizerConfig = None):
        self.spec = spec
        self.agent_memory = agent_memory
        # create the jinja 2 environment with PROMPT as the template
        self.env = Environment(undefined=StrictUndefined)
        self.template = self.env.from_string(PROMPT)

    async def summarize_messages(self, call_context: CallContext, existing_messages: List[LLMMessage], llm_unit: LLMUnit) -> LLMMessage:
        """
        Summarizes a list of messages into a single message using a new thread from the cpu.

        Args:
            call_context (CallContext): The current call context
            existing_messages (List[LLMMessage]): The list of messages to summarize
            llm_unit (LLMUnit): The llm unit to use to execute the llm

        Returns:
            LLMMessage: The summary of the messages
        """

        # format prompt with word limit and existing_messages using jinja2
        message = self.template.render(WORD_LIMIT=self.spec.summary_word_limit, messages=existing_messages)
        summarizer_message = SystemMessage(content=message)

        assistant_message = await llm_unit.execute_llm(call_context, [summarizer_message], [], MessageSummary.model_json_schema())

        return assistant_message