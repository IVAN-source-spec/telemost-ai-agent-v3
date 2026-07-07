import os


def get_node_id() -> str:
    return os.getenv("BOT_NODE_ID", "local-node")


def get_node_name() -> str:
    return os.getenv("BOT_NODE_NAME", get_node_id())


def get_global_bot_id(bot_id: str) -> str:
    return f"{get_node_id()}:{bot_id}"
