from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.sql import func
from .database import Base

class SMS(Base):
    __tablename__ = "sms_messages"
    id = Column(Integer, primary_key=True, index=True)
    sender = Column(String, index=True)
    text = Column(String)
    received_at = Column(DateTime(timezone=True), server_default=func.now())