from abc import ABC, abstractmethod
from src.models import Alert

class Notifier(ABC):
    @abstractmethod
    def send(self, alerts: list[Alert]) -> None:
        pass
