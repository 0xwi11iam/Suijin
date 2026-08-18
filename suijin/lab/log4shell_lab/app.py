"""Log4Shell Lab — vulnerable Log4j instance on port 5903."""
from flask import Flask, request
app = Flask(__name__)

@app.route("/")
def index():
    user_agent = request.headers.get("User-Agent","")
    # Simulated: real Log4j would evaluate ${jndi:ldap://...} here
    if "${jndi:" in user_agent.lower() or "${java:" in user_agent.lower():
        return f"Log4j evaluated! FLAG{{log4shell_rce_5903}}\nJNDI injection received: {user_agent}"
    return "<h1>Vulnerable App</h1><p>Log4j 2.14.1 running. Send JNDI payload in User-Agent header.</p>"

app.run(host="0.0.0.0", port=5903, debug=False)
