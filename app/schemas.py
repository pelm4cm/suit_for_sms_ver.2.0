from pydantic import BaseModel

class SMSCreate(BaseModel):
    sender: str
    text: str