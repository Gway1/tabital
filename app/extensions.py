# app/extensions.py
from flask_sqlalchemy import SQLAlchemy
from flask_marshmallow import Marshmallow
import flask_praetorian
from flask_mail import Mail

db = SQLAlchemy()
ma = Marshmallow()
guard = flask_praetorian.Praetorian()
mail = Mail()
