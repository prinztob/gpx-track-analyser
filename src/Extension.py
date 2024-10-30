from dataclasses import dataclass
from typing import Dict


@dataclass
class Extension:
    distance: float = 0.0
    cadence: int = 0
    power: int = 0
    speed: float = 0.0
    hr: int = 0
    slope: float = 0.0
    verticalVelocity: float = 0.0

    def to_dict(self) -> Dict:
        values = {"distance": round(self.distance, 1)}
        if self.cadence > 0:
            values["cadence"] = self.cadence
        if self.power > 0:
            values["power"] = self.power
        if self.speed > 0.0:
            values["speed"] = self.speed
        if self.hr > 0:
            values["hr"] = self.hr
        if self.slope != 0.0:
            values["slope"] = self.slope
        if self.verticalVelocity != 0.0:
            values["verticalVelocity"] = self.verticalVelocity
        return values

    @staticmethod
    def parse(extensions):
        extension = Extension()
        failed_count = 0
        for el in extensions[0]:
            try:
                if 'distance' in el.tag:
                    extension.distance = float(el.text)
                elif "power" in el.tag:
                    extension.power = int(el.text)
                elif "cad" in el.tag or "cadence" in el.tag:
                    extension.cadence = int(el.text)
                elif "speed" in el.tag:
                    extension.speed = float(el.text)
                elif "hr" in el.tag:
                    extension.hr = int(el.text)
                elif "slope" in el.tag:
                    extension.slope = float(el.text)
                elif "vvelocity" in el.tag:
                    extension.verticalVelocity = float(el.text)
            except:
                failed_count += 1
        if failed_count > 0:
            print(f"Could not parse {failed_count} entries.")
        return extension
