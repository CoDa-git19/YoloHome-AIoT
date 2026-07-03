from abc import ABC, abstractmethod
from typing import Dict, List, Any

# --- INTERFACES ---
class Observer(ABC):
    @abstractmethod
    def update(self, sensor_data: Dict[str, Any]):
        """This function will be called when new sensor data is available."""
        pass

# --- Base class ---
class Subject(ABC):
    def __init__(self):
        self._observers: List[Observer] = []

    def attach(self, observer: Observer):
        """Add a new subscriber"""
        if observer not in self._observers:
            self._observers.append(observer)

    def detach(self, observer: Observer):
        """Remove a Subscriber"""
        self._observers.remove(observer)

    def notify(self, sensor_data: Dict[str, Any]):
        """Notify all Subscribers of new sensor data"""
        for observer in self._observers:
            observer.update(sensor_data)

# --- IMPLEMENTATIONS ---
# # Example
# class AdafruitPublisher(Observer):
#     def update(self, sensor_data: Dict[str, Any]):
#         print(f"[Adafruit IO] Upload data to the cloud: {sensor_data}")

# class ThresholdChecker(Observer):
#     def update(self, sensor_data: Dict[str, Any]):
#         if sensor_data.get("temperature", 0) > 35.0:
#             print("[WARNING] Excessive temperature—fan automatically turned on!")

# class DashboardUpdater(Observer):
#     def update(self, sensor_data: Dict[str, Any]):
#         print(f"[Dashboard UI] Update chart with data: {sensor_data}")