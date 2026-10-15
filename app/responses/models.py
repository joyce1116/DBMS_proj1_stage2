from app.surveys.models import NormalizedAnswer, QuestionId, StrictModel, SubmissionReceipt


class Respondent(StrictModel):
    id: str
    username: str


class ResponsePublic(SubmissionReceipt):
    respondent: Respondent | None
    answers: list[NormalizedAnswer]


class ResponseList(StrictModel):
    items: list[ResponsePublic]
    total: int
    offset: int
    limit: int


class OptionStatistic(StrictModel):
    option_id: str
    label: str
    count: int


class ContentItem(StrictModel):
    response_id: str
    value: str | int | float


class QuestionStatistic(StrictModel):
    question_id: QuestionId
    title: str
    type: str
    visited_count: int
    answer_count: int
    options: list[OptionStatistic]
    average: float | None


class QuestionStatisticDetail(QuestionStatistic):
    contents: list[ContentItem]
    contents_total: int
    offset: int
    limit: int


class SurveyStatistics(StrictModel):
    survey_id: str
    total_responses: int
    questions: list[QuestionStatistic]
