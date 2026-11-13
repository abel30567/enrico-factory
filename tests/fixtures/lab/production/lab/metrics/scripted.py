"""A scripted metric for the lab tests: pops the first line of the file $SCRIPTED_METRIC and prints it.

`floor` exits 1 (the tree breaks a frozen floor); `tree` prints the first line of
$FACTORY_LAB_TREE/score.txt; an empty script exits 3.
"""

import os
import sys

path = os.environ["SCRIPTED_METRIC"]
with open(path, encoding="utf-8") as f:
    lines = f.read().splitlines()
if not lines:
    sys.exit(3)
with open(path, "w", encoding="utf-8") as f:
    f.write("".join(line + "\n" for line in lines[1:]))
first = lines[0].strip()
if first == "floor":
    sys.exit(1)
if first == "tree":
    with open(os.path.join(os.environ["FACTORY_LAB_TREE"], "score.txt"), encoding="utf-8") as f:
        first = f.read().splitlines()[0]
print(first)
