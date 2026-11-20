"""Correct the three remaining bbox training reference images after full-size review."""
import repair_remaining_antennae as repair
from build_projects import WORK

repair.FOLDER = WORK / 'state/training_bounds_repair'
repair.REPAIR_TAG = 'training_bounds_v3'
repair.FIXES = {
    '763490149': [(.313, .0839788622, .5982778645, .9145314558)],
    '763530743': [(.272, .235, .6272867925, .4364062598)],
    # Same six butterflies, in the same order. No hidden parts are reconstructed.
    '761031680': [
        (.7507899388, 0, .907, .1864768237),
        (.518, .404, .747, .682),
        (.138, .3508201091, .3703859214, .5668161576),
        (.277, .5974095214, .536, .7580286019),
        (.3396671106, .3723342533, .581, .681),
        (.3680808345, .247, .581, .586),
    ],
}
repair.GROUPS = {'bbox_training': ['763490149', '761031680', '763530743']}

if __name__ == '__main__':
    repair.main()
