from api.birthday_api import app
from api.player_profile_api import app as profile_app

app.mount("/profiles", profile_app)
