from pydantic import BaseModel, ConfigDict, Field


class ConfigResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    generation_mode: str = Field(serialization_alias="generationMode")
