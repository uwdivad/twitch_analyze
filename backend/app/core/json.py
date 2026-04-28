from typing import Any

import orjson


def dumps(value: Any) -> str:
    return orjson.dumps(value, option=orjson.OPT_SORT_KEYS).decode("utf-8")


def loads(value: str | bytes) -> Any:
    return orjson.loads(value)
