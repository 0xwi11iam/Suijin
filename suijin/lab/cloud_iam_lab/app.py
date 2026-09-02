"""Cloud IAM Lab — simulated AWS IAM misconfigurations on port 5900."""
from flask import Flask, jsonify

app = Flask(__name__)
app.config["SECRET_KEY"] = "cloud_lab_secret_5900"

INDEX = """<html><body><h1>AWS Management Console (Simulated)</h1>
<ul><li><a href="/s3">S3 Buckets</a></li><li><a href="/iam/users">IAM Users</a></li>
<li><a href="/metadata">Instance Metadata</a></li><li><a href="/lambda">Lambda Functions</a></li></ul>
<p style="color:gray;font-size:10px">AWS_ACCESS_KEY_ID=AKIALABDEMOEXAMPLE</p></body></html>"""

@app.route("/")
def index(): return INDEX

@app.route("/s3")
def s3():
    buckets = {"demo-public-bucket": "public-read", "demo-private-bucket": "private",
               "demo-logs-2024": "authenticated-read"}
    return jsonify(buckets)

@app.route("/s3/<bucket>")
def s3_bucket(bucket):
    if "public" in bucket: return f"Contents of {bucket}: flag{{public_bucket_leak}}\nuser_credentials.csv\nbackup.sql"
    return f"Access denied to {bucket}"

@app.route("/iam/users")
def iam_users():
    return jsonify([{"UserName":"admin","Policies":["AdministratorAccess"]},
                    {"UserName":"dev-readonly","Policies":["AmazonS3ReadOnlyAccess"]},
                    {"UserName":"backup-svc","Policies":["AmazonS3FullAccess","IAMReadOnlyAccess"]}])

@app.route("/metadata")
def metadata(): return "ami-id: ami-0c55b159cbfafe1f0\ninstance-id: i-1234567890abcdef0\npublic-ipv4: 54.1.2.3\nFLAG: FLAG{metadata_ssrf_leak}"

@app.route("/lambda/functions")
def lambda_list(): return jsonify(["process-payments", "user-sync", "backup-db"])

@app.route("/lambda/<func>/env")
def lambda_env(func):
    if func == "process-payments":
        return jsonify({"STRIPE_SECRET_KEY":"sk_live_demo_lambda_5900","DB_PASSWORD":"lambda_db_pass_123"})
    return jsonify({"env": "production", "debug": "false"})

@app.route("/flag")
def flag(): return "FLAG{cloud_iam_admin_5900}"

app.run(host="0.0.0.0", port=5900, debug=False)
