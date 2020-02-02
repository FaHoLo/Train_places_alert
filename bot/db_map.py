from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.ext.declarative import declarative_base


Base = declarative_base()

class ActiveSearch(Base):
    __tablename__ = 'Active searches'
    id = Column(Integer, primary_key=True)
    chat_id = Column(Integer)
    url = Column(String(500))
    train_numbers = Column(String)
    price_limit = Column(Integer)
    query_time = Column(DateTime)
    
    def __repr__(self):
        return f'<ActiveSearch(id={self.chat_id}, trains={self.train_numbers}, time={self.query_time})>'


class SearchLog(Base):
    __tablename__ = 'Searches log'
    id = Column(Integer, primary_key=True)
    chat_id = Column(Integer)
    url = Column(String(500))
    train_numbers = Column(String)
    price_limit = Column(Integer)
    query_time = Column(DateTime)
    
    def __repr__(self):
        return f'<LoggedSearch(id={self.chat_id}, trains={self.train_numbers}, time={self.query_time})>'
