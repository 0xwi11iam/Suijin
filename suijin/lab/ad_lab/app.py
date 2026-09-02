"""Active Directory Lab — simulated DC with Kerberos, LDAP, SMB on port 5905."""
from flask import Flask, jsonify, request

app = Flask(__name__)
users = {"administrator": "P@ssw0rd2024!", "jsmith": "Summer2024!", "backup_svc": "Backup123!", "sql_svc": "SqlSvc2024!"}
flags = {}

@app.route("/")
def index():
    return "<h1>DC01 — Active Directory Domain Controller (Simulated)</h1><ul><li><a href='/kerberos'>Kerberos</a></li><li><a href='/ldap'>LDAP</a></li><li><a href='/smb'>SMB Shares</a></li><li><a href='/flag'>Flag</a></li></ul>"

@app.route("/kerberos/asrep")
def asrep():
    # Simulated: users without pre-auth
    return jsonify({"users_without_preauth": ["backup_svc"], "hash_format": "$krb5asrep$23$backup_svc@DC01.LOCAL:hash_here"})

@app.route("/kerberos/tgt")
def tgt():
    user = request.args.get("user","")
    if user in users: return jsonify({"tgt_available": True, "user": user, "domain": "DC01.LOCAL"})
    return jsonify({"error": "user not found"}), 404

@app.route("/ldap/search")
def ldap_search():
    filt = request.args.get("filter","(objectClass=*)")
    if "admin" in filt.lower() or "administrator" in filt.lower():
        return jsonify({"dn": "CN=Administrator,CN=Users,DC=DC01,DC=LOCAL", "memberOf": ["Domain Admins", "Enterprise Admins", "Schema Admins"]})
    return jsonify([{"dn": f"CN={u},CN=Users,DC=DC01,DC=LOCAL", "sAMAccountName": u} for u in users])

@app.route("/smb/shares")
def smb_shares():
    return jsonify({"shares": [{"name":"NETLOGON","access":"read"},{"name":"SYSVOL","access":"read"},{"name":"Backup","access":"authenticated"},{"name":"HR-Docs","access":"admin"}]})

@app.route("/smb/share/<name>")
def smb_share(name):
    if name.lower() == "backup":
        return "Contents of Backup share:\nbackup_svc_creds.txt: backup_svc:Backup123!\nflag{ad_backup_share_5905}"
    if name.lower() == "hr-docs" and request.headers.get("X-Auth-User") == "administrator":
        return "HR Documents:\nflag{ad_hr_share_5905}\nsalary_data.xlsx"
    return f"Access denied to {name}. Auth required: {request.headers.get('X-Auth-User','none')}"

@app.route("/flag")
def flag(): return "FLAG{ad_domain_admin_5905}"

app.run(host="0.0.0.0", port=5905, debug=False)
