# bugscope

Pull LIVE program scopes from bug-bounty platforms (bbscope method):
h1 (HackerOne, API token as username:token), bugcrowd (session cookie
token), ywh (YesWeHack bearer), intigriti (bearer), immunefi (bearer).
Results land in workspace outputs/bugscope/<platform>.json; scope_search
queries them offline afterward. All requests SSL-verified with a
realistic browser identity — credentials come from the operator, are
only sent to the owning platform's API host, and are never logged.
