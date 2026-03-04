from agentuniverse.agent_serve.web.web_booster import start_web_server
from agentuniverse.base.agentuniverse import AgentUniverse


class ServerApplication:
    """Server application for QQ Social Bot agent."""

    @classmethod
    def start(cls):
        AgentUniverse().start()
        start_web_server()


if __name__ == "__main__":
    ServerApplication.start()
