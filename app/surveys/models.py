"""Validated draft survey contracts; stable question/option IDs survive editing."""

from datetime import datetime, timezone
from math import ceil, floor, isfinite
from typing import Annotated, Any, Literal
from unicodedata import normalize

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StringConstraints,
    TypeAdapter,
    field_validator,
    model_validator,
)


SurveyTitle = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=200)
]
QuestionTitle = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=500)
]
OptionLabel = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=200)
]
Description = Annotated[str, StringConstraints(strict=True, max_length=5000)]
QuestionId = Annotated[str, StringConstraints(strict=True, pattern=r"^q_[a-f0-9]{32}$")]
OptionId = Annotated[str, StringConstraints(strict=True, pattern=r"^opt_[a-f0-9]{32}$")]
Revision = Annotated[StrictInt, Field(ge=1, le=2147483647)]
NonnegativeInt = Annotated[StrictInt, Field(ge=0)]


def _finite_number(value: object) -> int | float:
    # Check the type before numeric coercion: bool is an int subclass in Python.
    if type(value) not in (int, float):
        raise ValueError("A numeric bound must be a JSON number")
    if abs(value) > 10**15 or not isfinite(value):
        raise ValueError("A numeric bound must be finite and within +/- 10^15")
    return value


NumericBound = Annotated[StrictInt | StrictFloat, BeforeValidator(_finite_number)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OptionInput(StrictModel):
    id: OptionId | None = None
    label: OptionLabel


class OptionPublic(OptionInput):
    id: OptionId


class QuestionBase(StrictModel):
    title: QuestionTitle
    required: StrictBool = False


class ChoiceQuestionBase(QuestionBase):
    options: Annotated[list[OptionInput], Field(min_length=2, max_length=50)]

    @model_validator(mode="after")
    def validate_options(self):
        labels = [normalize("NFKC", option.label).casefold() for option in self.options]
        if len(labels) != len(set(labels)):
            raise ValueError("Option labels must be unique, including Unicode/case equivalents")
        ids = [option.id for option in self.options if option.id is not None]
        if len(ids) != len(set(ids)):
            raise ValueError("Option IDs must be unique within a question")
        return self


class SingleChoiceInput(ChoiceQuestionBase):
    type: Literal["single_choice"]


class MultipleChoiceInput(ChoiceQuestionBase):
    type: Literal["multiple_choice"]
    min_selections: NonnegativeInt = 0
    max_selections: NonnegativeInt | None = None

    @model_validator(mode="after")
    def validate_selection_bounds(self):
        maximum = len(self.options) if self.max_selections is None else self.max_selections
        if not self.min_selections <= maximum <= len(self.options):
            raise ValueError("Selection bounds must satisfy 0 <= min <= max <= option count")
        if self.required and maximum < 1:
            raise ValueError("A required multiple-choice question must allow at least one selection")
        return self


class TextQuestionInput(QuestionBase):
    type: Literal["text"]
    min_length: NonnegativeInt = 0
    max_length: Annotated[StrictInt, Field(ge=1, le=10000)] = 1000

    @model_validator(mode="after")
    def validate_length_bounds(self):
        if self.min_length > self.max_length:
            raise ValueError("Minimum text length cannot exceed maximum text length")
        return self


class NumberQuestionBase(QuestionBase):
    type: Literal["number"]
    min_value: NumericBound | None = None
    max_value: NumericBound | None = None
    integer_only: StrictBool = False

    @model_validator(mode="after")
    def validate_number_bounds(self):
        if self.min_value is not None and self.max_value is not None:
            if self.min_value > self.max_value:
                raise ValueError("Minimum numeric value cannot exceed maximum numeric value")
            if self.integer_only and ceil(self.min_value) > floor(self.max_value):
                raise ValueError("The numeric interval must contain at least one integer")
        return self


class NumberQuestionInput(NumberQuestionBase):
    @model_validator(mode="after")
    def validate_integer_bounds(self):
        if self.integer_only:
            for name in ("min_value", "max_value"):
                value = getattr(self, name)
                if value is not None:
                    if isinstance(value, float) and not value.is_integer():
                        raise ValueError("Integer question bounds must be integers")
                    setattr(self, name, int(value))
        return self


QuestionInput = Annotated[
    SingleChoiceInput | MultipleChoiceInput | TextQuestionInput | NumberQuestionInput,
    Field(discriminator="type"),
]


class SingleChoicePublic(SingleChoiceInput):
    id: QuestionId
    options: Annotated[list[OptionPublic], Field(min_length=2, max_length=50)]


class MultipleChoicePublic(MultipleChoiceInput):
    id: QuestionId
    options: Annotated[list[OptionPublic], Field(min_length=2, max_length=50)]


class TextQuestionPublic(TextQuestionInput):
    id: QuestionId


class NumberQuestionPublic(NumberQuestionBase):
    # Historical surveys allowed fractional bounds on integer answers. Keep
    # those records readable; configuration writes and publication are stricter.
    id: QuestionId


QuestionPublic = Annotated[
    SingleChoicePublic | MultipleChoicePublic | TextQuestionPublic | NumberQuestionPublic,
    Field(discriminator="type"),
]


def parse_question(value: object):
    return TypeAdapter(QuestionInput).validate_python(value)


def _future_deadline(value: datetime | None) -> datetime | None:
    if value is not None and value <= datetime.now(timezone.utc):
        raise ValueError("Deadline must be in the future")
    return value


class SurveyCreate(StrictModel):
    title: SurveyTitle
    description: Description = ""
    allow_anonymous: StrictBool = False
    allow_repeat: StrictBool = False
    deadline: AwareDatetime | None = None

    @field_validator("deadline")
    @classmethod
    def validate_deadline(cls, value: datetime | None) -> datetime | None:
        return _future_deadline(value)


class SurveyPatch(StrictModel):
    expected_revision: Revision
    title: SurveyTitle | None = None
    description: Description | None = None
    allow_anonymous: StrictBool | None = None
    allow_repeat: StrictBool | None = None
    deadline: AwareDatetime | None = None

    @field_validator("deadline")
    @classmethod
    def validate_deadline(cls, value: datetime | None) -> datetime | None:
        return _future_deadline(value)

    @model_validator(mode="after")
    def validate_changes(self):
        changes = self.model_fields_set - {"expected_revision"}
        if not changes:
            raise ValueError("At least one survey metadata field must be supplied")
        if any(getattr(self, field) is None for field in changes - {"deadline"}):
            raise ValueError("Only deadline may be explicitly null")
        return self


class QuestionOrder(StrictModel):
    question_ids: Annotated[list[QuestionId], Field(max_length=100)]

    @field_validator("question_ids")
    @classmethod
    def validate_unique_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("Question order cannot contain duplicate IDs")
        return value


class QuestionSelection(StrictModel):
    # Malformed/unknown IDs deliberately share the owner-scoped 404 response;
    # only the body shape is validated here.
    version_id: Annotated[str, StringConstraints(strict=True)]


class QuestionOptionMap(StrictModel):
    local_id: OptionId
    source_id: OptionId


class QuestionSource(StrictModel):
    question_id: QuestionId
    family_id: str
    version_id: str
    version: Revision
    option_map: Annotated[list[QuestionOptionMap], Field(max_length=50)]


class SurveyTransition(StrictModel):
    expected_revision: Revision


class RuleBase(StrictModel):
    source_id: QuestionId
    target_id: QuestionId | Literal["end"]


class ChoiceRule(RuleBase):
    kind: Literal["choice"]
    operator: Literal["eq", "contains_any", "contains_all"]
    option_ids: Annotated[list[OptionId], Field(min_length=1, max_length=50)]

    @field_validator("option_ids")
    @classmethod
    def unique_options(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("Rule options must be unique")
        return value


class NumberRule(RuleBase):
    kind: Literal["number"]
    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte"]
    value: NumericBound


JumpRule = Annotated[ChoiceRule | NumberRule, Field(discriminator="kind")]


class RulesPatch(StrictModel):
    expected_revision: Revision
    rules: Annotated[list[JumpRule], Field(max_length=200)]


class AnswerBatch(StrictModel):
    survey_revision: Revision
    answers: Annotated[dict[QuestionId, Any], Field(max_length=100)]


class SubmissionInput(AnswerBatch):
    request_id: Annotated[str, StringConstraints(strict=True, pattern=r"^[A-Za-z0-9_-]{8,64}$")]
    anonymous: StrictBool = False


class NormalizedAnswer(StrictModel):
    question_id: QuestionId
    value: str | list[str] | int | float | None


class NavigationResult(StrictModel):
    path: list[QuestionId]
    answers: list[NormalizedAnswer]
    completed: bool
    next_question_id: QuestionId | None


class SubmissionReceipt(StrictModel):
    response_id: str
    survey_id: str
    survey_revision: int
    submitted_at: datetime
    anonymous: bool
    path: list[QuestionId]


class SurveySummary(StrictModel):
    id: str
    owner_id: str
    title: str
    description: str
    allow_anonymous: bool
    allow_repeat: bool
    deadline: datetime | None
    status: Literal["draft", "published", "closed"]
    availability: Literal["draft", "open", "expired", "closed"]
    is_open: bool
    share_id: str | None
    share_url: str | None
    published_at: datetime | None
    closed_at: datetime | None
    revision: int
    question_count: int
    created_at: datetime
    updated_at: datetime


class SurveyDetail(SurveySummary):
    questions: list[QuestionPublic]
    jump_rules: list[JumpRule]
    question_sources: list[QuestionSource]


class SurveyList(StrictModel):
    items: list[SurveySummary]
    total: int
    offset: int
    limit: int


class SharedSurvey(StrictModel):
    """Only question content and settings needed by a signed-in recipient."""

    id: str
    title: str
    description: str
    allow_anonymous: bool
    allow_repeat: bool
    deadline: datetime | None
    revision: int
    questions: list[QuestionPublic]
    jump_rules: list[JumpRule]


class AnswerCheck(StrictModel):
    question_id: QuestionId
    value: Any


class AnswerChecked(StrictModel):
    question_id: QuestionId
    valid: Literal[True] = True
    value: str | list[str] | int | float | None
