#!/usr/bin/env python3
# coding=utf-8
"""A simple example demonstrating how to use Argparse to support subcommands.


This example shows an easy way for a single command to have many subcommands, each of which takes different arguments
and provides separate contextual help.
"""
import json
from typing import List, Dict

import cmd2
from cmd2 import style, Fg, Bg
from rich.console import Console

from eidolon_cli.client import EidolonClient


class SubcommandsExample(cmd2.Cmd):
    CUSTOM_CATEGORY = 'Eidolon CLI'

    """
    Example cmd2 application where we a base command which has a couple subcommands
    and the "sport" subcommand has tab completion enabled.
    """

    def __init__(self):
        super().__init__(
            multiline_commands=['echo'],
            persistent_history_file='eidolon_history.dat',
            # startup_script='scripts/startup.txt',
            include_ipy=False,
        )
        del cmd2.Cmd.do_py  # disable the "py" command
        del cmd2.Cmd.do_shell
        del cmd2.Cmd.do_ipy
        del cmd2.Cmd.do_run_pyscript
        del cmd2.Cmd.do_edit
        del cmd2.Cmd.do_shortcuts
        self.console = Console()

        self.intro = style('Eidolon command line tool. Type help for the list of commands.', fg=Fg.RED, bg=Bg.WHITE, bold=True)

        # Allow access to your application in py and ipy via self
        self.self_in_py = False

        # Set the default category name
        self.default_category = "Builtin Tools"

        self.client = EidolonClient()
        self.agents = [f"{agent.name}/{agent.program}" for agent in self.client.agent_programs]
        self.programs = [f"{agent.name}/{agent.program}" for agent in self.client.agent_programs if agent.is_program]

    def endpoints_provider(self) -> List[str]:
        """A choices provider is useful when the choice list is based on instance data of your application"""
        return self.agents

    def programs_provider(self) -> List[str]:
        """A choices provider is useful when the choice list is based on instance data of your application"""
        return self.programs

    info_parser = cmd2.Cmd2ArgumentParser()
    info_arg = info_parser.add_argument('endpoint', help='Enter the name of the endpoint', choices_provider=endpoints_provider, nargs='?')

    @cmd2.with_category(CUSTOM_CATEGORY)
    @cmd2.with_argparser(info_parser)
    def do_info(self, arg):
        """Show information about agents endpoints"""
        if not arg.endpoint or len(arg.endpoint) == 0:
            for agent in self.client.agent_programs:
                self.console.print(agent)
        else:
            agent = self.client.get_client(arg.endpoint)
            self.console.print(agent)
            self.console.print(agent.schema)

    start_parser = cmd2.Cmd2ArgumentParser()
    start_arg = start_parser.add_argument('endpoint', help='Enter the name of the endpoint', choices_provider=programs_provider)

    @cmd2.with_category(CUSTOM_CATEGORY)
    @cmd2.with_argparser(start_parser)
    def do_start(self, arg):
        """Start a process with an agent"""
        agent = self.client.get_client(arg.endpoint)
        process_id = None
        self.have_conversation(agent, process_id)

    def have_conversation(self, agent, process_id):
        current_conversation = agent.name + "/" + agent.program
        while True:
            if current_conversation:
                print(f"Conversation: {current_conversation}")
                user_input = agent.schema.await_input(self.console)
                if user_input is None:
                    self.console.print()
                    break
                status_code, response = self.client.send_request(agent, user_input, process_id)
                if status_code != 200:
                    self.console.print(f"Error: {str(response)}")
                else:
                    self.console.print(f"{str(response)}")

                    if isinstance(response["data"], dict):
                        self.console.print_json(json.dumps(response["data"]))
                    else:
                        self.console.print(response["data"], style="blue")

                    if response["state"] == "terminated":
                        break
                    else:
                        actions = response["available_actions"]
                        if len(actions) > 1:
                            action = ""
                            valid_input = False
                            while not valid_input:
                                self.console.print(f"action [{','.join(actions)}]: ", markup=False, end="")
                                action = self.console.input()
                                if action in actions:
                                    valid_input = True
                                else:
                                    self.console.print("Invalid action")
                            current_conversation = agent.name + "/" + action
                        elif len(actions) == 1:
                            action = actions[0]
                            current_conversation = agent.name + "/" + action
                        # else leave current_conversation as is
                        process_id = response["process_id"]

    def agent_provider(self) -> List[str]:
        """A choices provider is useful when the choice list is based on instance data of your application"""
        agents = [agent.name for agent in self.client.agent_programs]
        # now deduplicate the list
        agent_names = list(set(agents))
        agent_names.sort(reverse=True)
        return agent_names

    def agent_process_id_provider(self, arg_tokens: Dict[str, List[str]]) -> List[str]:
        """A choices provider is useful when the choice list is based on instance data of your application"""
        processes = self.client.get_processes(arg_tokens["agent"][0])
        # now we just want the processes in the "idle" state and just the process id
        process_ids = [process["process_id"] for process in processes if process["state"] != "terminated"]
        return process_ids

    resume_parser = cmd2.Cmd2ArgumentParser()
    resume_arg = resume_parser.add_argument('agent', help='Enter the name of the agent', choices_provider=agent_provider)
    resume_parser.add_argument('process_id', help='Enter the process id', choices_provider=agent_process_id_provider)

    @cmd2.with_category(CUSTOM_CATEGORY)
    @cmd2.with_argparser(resume_parser)
    def do_resume(self, arg):
        """Resume a process with an agent"""
        agent = self.client.get_client(arg.agent)
        self.have_conversation(agent, arg.process_id)