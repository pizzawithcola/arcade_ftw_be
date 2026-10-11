from typing import Any, Protocol


class QuestionType(Protocol):
    """Contract every question type implements. Register it in QUESTION_TYPES."""

    type_key: str

    def render(self, question: dict[str, Any]) -> dict[str, Any]:
        """Payload sent to the frontend."""
        ...

    def accept(self, session: Any, answer: Any) -> bool:
        """Whether the incoming answer is accepted."""
        ...

    def score(self, answers: list[Any]) -> dict[str, Any]:
        """Scoring result. Keep this a pure function."""
        ...

    def timeout_behavior(self) -> str:
        """e.g. 'auto_submit' or 'discard'."""
        ...
