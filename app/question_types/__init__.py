from app.question_types.base import QuestionType
from app.question_types.choice import ChoiceType

QUESTION_TYPES: dict[str, QuestionType] = {
    ChoiceType.type_key: ChoiceType(),
}
