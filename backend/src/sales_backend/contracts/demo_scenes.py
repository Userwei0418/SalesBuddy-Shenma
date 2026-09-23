from pydantic import BaseModel, ConfigDict, Field, field_validator


class DemoSceneCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)

    @field_validator("name")
    @classmethod
    def name_required(cls, value):
        if not value.strip():
            raise ValueError("请填写场景名称")
        return value.strip()


class DemoSceneUpdate(DemoSceneCreate):
    version_no: int = Field(ge=1)


class DemoSceneBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenes: list[DemoSceneCreate] = Field(min_length=1, max_length=20)


class DemoSceneDelete(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_no: int = Field(ge=1)
