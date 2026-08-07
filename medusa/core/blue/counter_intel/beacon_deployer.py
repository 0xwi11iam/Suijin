"""JS beacon deployer — track attacker's browser."""
def generate_beacon_js() -> str:
    return """
<script>
(function(){var d=document,f=[];d.addEventListener('click',function(e){f.push({t:e.target.tagName,x:e.clientX,y:e.clientY,ts:Date.now()});if(f.length>10){var i=new Image();i.src='/__beacon__.gif?d='+btoa(JSON.stringify(f));f=[]}});var i=new Image();i.src='/__beacon__.gif?init=1&r='+d.referrer+'&u='+navigator.userAgent;})();
</script>"""

def inject_beacon(response_body: str) -> str:
    beacon = generate_beacon_js()
    if "</body>" in response_body:
        return response_body.replace("</body>", beacon + "</body>")
    return response_body + beacon
