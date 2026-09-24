"""Column types shared by the SQLite models."""

import json

from sqlalchemy import Text, func
from sqlalchemy.types import TypeDecorator


class JSONB(TypeDecorator):
    """SQLite JSONB: bound through jsonb(), read back through json()."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else json.dumps(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value if isinstance(value, dict) else json.loads(value)

    def bind_expression(self, bindvalue):
        return func.jsonb(bindvalue)

    def column_expression(self, col):
        # type_=self is load-bearing: with Text the result processor is skipped and reads return raw JSON text.
        return func.json(col, type_=self)
