import unreal

# Hand-written for an eval case; never run.
# Shot Q1-2 of ep01 at 24 fps, frames 12 to 131. A key is (first frame, last frame, actor, pose);
# the engine eases from one key to the next. A line is (line, speaker, first frame).
KEYS = [
    (12, 17, "asset:wen", "holds at x 0, the lantern in the right hand, and looks at Dax"),
    (12, 65, "asset:dax", "stands at x 1.6 and looks out to sea"),
    (18, 23, "asset:wen", "anticipation: the weight goes back onto the left foot"),
    (24, 47, "asset:wen", "steps to x 0.8 and settles; the lantern swings once and stops"),
    (48, 77, "asset:wen", "holds the lantern out to Dax"),
    (66, 83, "asset:dax", "turns, reaches slowly and takes the lantern by the handle at x 1.4; Wen lets go"),
    (84, 131, "asset:dax", "draws the lantern in and holds it, looking back out to sea"),
    (84, 131, "asset:wen", "holds and watches Dax"),
]
LINES = [
    ("L1", "asset:wen", 48),
    ("L2", "asset:dax", 84),
]

for first, last, actor, pose in KEYS:
    unreal.log(f"key {first}-{last} {actor}: {pose}")
for line, speaker, first in LINES:
    unreal.log(f"line {line} {speaker} from frame {first}")
