"""Intelligence modules — knowledge graph, oracle, supervisor."""

from suijin.intel.drift_analyser import analyse_drift
from suijin.intel.knowledge_graph import *
from suijin.intel.oracle import detect_anomaly, diagnose, set_providers
from suijin.intel.supervisor import evaluate, format_spend, render_panel
from suijin.intel.supervisor import set_providers as sup_set_providers
