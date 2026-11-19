from auditenv.policies.base import Policy
from auditenv.policies.random_explorer import RandomExplorerPolicy
from auditenv.policies.reconcilers import ReferenceSolverPolicy, SimpleReconcilerPolicy
from auditenv.policies.simple import FlagEverythingPolicy, OraclePolicy, SilencePolicy

POLICIES = {
    p.name: p
    for p in (RandomExplorerPolicy, SimpleReconcilerPolicy, FlagEverythingPolicy, OraclePolicy,
              SilencePolicy, ReferenceSolverPolicy)
}
OFFICIAL_BASELINES = ("random_explorer", "simple_reconciler", "flag_everything", "oracle")

__all__ = ["POLICIES", "OFFICIAL_BASELINES", "Policy"]
