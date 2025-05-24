from typing import Any, Optional
from pydantic import BaseModel, Field

class WebSocketMessage(BaseModel):
    client_id: str = Field(..., min_length=1, description="Unique identifier for the client.")
    status: Optional[dict[str, Any]] = Field(None, description="Optional dictionary of status attributes.")

# Example of a more specific status model if needed in the future
# class ClientStatus(BaseModel):
#     connected: Optional[bool] = None
#     battery_level: Optional[float] = Field(None, ge=0, le=100)
#     current_task: Optional[str] = None

# class WebSocketMessageWithSpecificStatus(BaseModel):
#     client_id: str = Field(..., min_length=1)
#     status: Optional[ClientStatus] = None
