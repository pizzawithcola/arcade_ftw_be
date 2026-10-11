from typing import Any


class ChoiceType:
    type_key = "choice"

    def render(self, question: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": self.type_key,
            "question_id": question.get("id"),
            "text": question.get("text", ""),
            "options": question.get("options", []),
        }

    def accept(self, session: Any, answer: Any) -> bool:
        return isinstance(answer, int)

    def score(self, answers: list[Any]) -> dict[str, Any]:
        return {"correct": sum(1 for answer in answers if answer is True), "total": len(answers)}

    def timeout_behavior(self) -> str:
        return "discard"
