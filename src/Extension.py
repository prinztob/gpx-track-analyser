from dataclasses import dataclass
from typing import Dict


@dataclass
class Extension:
    distance: float = 0.0
    cadence: int = 0
    power: int = 0
    power60s: int = 0
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
        if self.power60s > 0:
            values["power60s"] = self.power60s
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
    def parse(extensionsFromGpx):
        extension = Extension()
        failed_count = 0
        if len(extensionsFromGpx) > 0:
            for el in extensionsFromGpx[0]:
                try:
                    if 'distance' in el.tag:
                        extension.distance = float(el.text)
                    elif "power" in el.tag:
                        extension.power = int(el.text)
                    elif "power60s" in el.tag:
                        extension.power60s = int(el.text)
                    elif "cad" in el.tag or "cadence" in el.tag:
                        extension.cadence = int(el.text)
                    elif "speed" in el.tag:
                        extension.speed = float(el.text)
                    elif "hr" in el.tag:
                        extension.hr = int(el.text)
                    elif "slope" in el.tag:
                        extension.slope = float(el.text)
                    elif "vvelocity" in el.tag or "verticalVelocity" in el.tag:
                        extension.verticalVelocity = float(el.text)
                except:
                    failed_count += 1
        return extension

    @staticmethod
    def parse_from_yaml(extensions: dict):
        extension = Extension()
        failed_count = 0
        for k, v in extensions.items():
            try:
                if 'distance' == k:
                    extension.distance = v
                elif "power" == k:
                    extension.power = v
                elif "power60s" == k:
                    extension.power60s = v
                elif "cad" == k or "cadence" == k:
                    extension.cadence = v
                elif "speed" == k:
                    extension.speed = v
                elif "hr" == k:
                    extension.hr = v
                elif "slope" == k:
                    extension.slope = v
                elif "vvelocity" == k or "verticalVelocity" == k:
                    extension.verticalVelocity = v
            except:
                failed_count += 1
        return extension
