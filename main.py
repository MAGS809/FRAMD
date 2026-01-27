from app import app, db, session
import models

with app.app_context():
    db.create_all()

from replit_auth import make_replit_blueprint

app.register_blueprint(make_replit_blueprint(), url_prefix="/auth")

@app.before_request
def make_session_permanent():
    session.permanent = True

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
