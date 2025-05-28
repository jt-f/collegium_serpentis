from enum import Enum
from typing import Literal, Optional
import uuid

from pydantic import BaseModel, Field

class CommandName(Enum):
    PAUSE = "PAUSE"
    RESUME = "RESUME"
    DISCONNECT = "DISCONNECT"

class CommandPayload(BaseModel):
    client_id: str
    command_name: CommandName
    message_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    data: Optional[dict] = None

class FrontendCommand(BaseModel):
    type: Literal["command"] = "command"
    payload: CommandPayload
