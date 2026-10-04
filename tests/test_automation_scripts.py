from __future__ import annotations

from uuid import uuid4

from sqlalchemy.dialects import postgresql

from app.services import automation_scripts


class _Result:
    def all(self) -> tuple[object, ...]:
        return ()


class _CapturingSession:
    statement: object

    def execute(self, statement: object) -> _Result:
        self.statement = statement
        return _Result()


def test_list_scripts_query_compiles_with_active_and_draft_versions() -> None:
    db = _CapturingSession()

    assert automation_scripts.list_scripts(db, tenant_id=uuid4()) == ()

    # The draft scalar subquery must retain its version-table FROM clause even
    # though the outer query joins the same table for the active version.
    db.statement.compile(dialect=postgresql.dialect())
