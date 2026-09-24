"""Intelligence modules — knowledge graph, oracle, supervisor."""

from suijin.modules.redteam.lib.intel.drift_analyser import analyse_drift
from suijin.modules.redteam.lib.intel.knowledge_graph import *
from suijin.modules.redteam.lib.intel.oracle import detect_anomaly, diagnose, set_providers
from suijin.modules.redteam.lib.intel.supervisor import evaluate, format_spend, render_panel
from suijin.modules.redteam.lib.intel.supervisor import set_providers as sup_set_providers
