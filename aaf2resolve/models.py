from dataclasses import dataclass
from typing import List, Optional
from fractions import Fraction

@dataclass
class Keyframe:
    time_frames: int
    pos_x: float = 0.0
    pos_y: float = 0.0
    scale_x: float = 1.0
    scale_y: float = 1.0
    rotation: float = 0.0

@dataclass
class Event:
    name: str
    rec_in_f: int
    rec_out_f: int
    duration_f: int
    source_path: Optional[str]
    source_name: Optional[str]
    effect_name: Optional[str]
    effect_convertible: bool
    keyframes: List[Keyframe]
    tape_id: Optional[str]
    disk_label: Optional[str]
    width: int
    height: int
    note: str = ""
    filler_fx_file: Optional[str] = None

@dataclass
class SequenceInfo:
    name: str
    start_tc_f: int
    fps: Fraction
    width: int
    height: int
    events: List[Event]
