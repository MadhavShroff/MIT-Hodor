import os
import scraper.slcm as scraper
import parser.parser as parser
import parser.responses as responses
import utils.dbase as database
import requests
import fbmq
from flask import Flask, request
from flask_sqlalchemy import SQLAlchemy
from wit import Wit


### CONFIGS ###
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ['DATABASE_URL']
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
page = fbmq.Page(os.environ["PAGE_ACCESS_TOKEN"])
wit_client = Wit(os.environ["WIT_TOKEN"])
dbase = database.handler(db)
responder = responses.messages()

### Quick Replies on messenger ###
quick_replies = [
        fbmq.QuickReply(title="What can you do?", payload="WHAT"),
        fbmq.QuickReply(title="Attendance", payload="ATTENDANCE"),
        fbmq.QuickReply(title="Bunk", payload="BUNK"),
        fbmq.QuickReply(title="Timetable", payload="TIMETABLE"),
        fbmq.QuickReply(title="Teacher Guardian", payload="TEACHER"),
    ]

### DB Skeleton ###
class User(db.Model):
    fbid = db.Column(db.String(80), primary_key=True)
    rollno = db.Column(db.String(80), unique=True, nullable=True)
    password = db.Column(db.String(80), nullable=True)
    group = db.Column(db.String(80), nullable=True)
    name = db.Column(db.String(80), nullable=True)
    semester = db.Column(db.String(2), nullable=True)


    def __init__(self, fbid, rollno=None, password=None, group=None, name=None, semester=None):
        self.fbid = fbid # Unique fbid
        self.rollno = rollno # User's SLCM reg no
        self.password = password # User's password
        self.group = group # User's group (Chem or Phy)
        self.name = name # User's name
        self.semester = semester # User's sem

    def __repr__(self):
        return '(<Name>{} <Rollno>{})'.format(self.name, self.rollno)

@page.handle_delivery
def delivery_handler(payload):
    print("Message delivered")

@page.handle_echo
def echo_handler(payload):
    print('Message echoed')

@page.handle_postback
def postback_handler(payload):
    print('postback pressed')

@page.after_send
def after_send(payload, response):
    print("Done")

@page.handle_read
def read_hanlder(payload):
    print("Message read by user")


### Handles Fb verification ###
@app.route('/', methods=['POST'])
def webhook():
    page.handle_webhook(request.get_data(as_text=True))
    return "ok"

### Main method (Handles user messages, db) ###
@page.handle_message
def message_handler(event):
    """:type event: fbmq.Event"""
    sender_id = event.sender_id
    message = event.message_text

    ### get user sending request ###
    client = User.query.filter_by(fbid=sender_id).first()

    if client is None:
        ### User doesn't exist on DB ###

        user = User(sender_id) # Create new user with fbid
        dbase.new_user(sender_id, user)
        page.send(sender_id, responder.menu)
        page.send(sender_id, responder.new_user)
    else:
        ### Fetch the created user through fbid ###

        user = User.query.filter_by(fbid = sender_id).first()

        if user.name is None:
            user_profile = page.get_user_profile(sender_id)

            if user_profile is not None:
                if 'first_name' in user_profile and 'last_name' in user_profile: 
                    user_name = "{} {}".format(user_profile['first_name'], user_profile['last_name'])
                elif 'name' in user_profile:
                    user_name = "{}".format(user_profile['name'])

                dbase.name(user_name, user)

        if user.rollno  == None:
            ### User has entered regno ###
            if message is None:
                message = "xyz"

            message = message[:80] # If message is above 80 chars, its most probably wrong.
            # Wrong details will be handled by driver
            dbase.regno(message, user)
            page.send(sender_id, responder.new_user_pass)

        elif user.password == None:
            ### User has entered password ###
            if message is None:
                message = "xyz"

            message = message[:80]
            dbase.password(message, user)

            try:
                check_driver = scraper.login(user.rollno, user.password)
                if check_driver is None:
                        ### Remove record if wrong details have been entered ###
                        ### Goes back to step 1 (Enter regno) ###
                        dbase.delete(user)
                        db.session.commit()
                        page.send(sender_id, responder.wrong)
                        page.send(sender_id, "Message me again to restart the registration")
                else:
                        group = scraper.group(check_driver)
                        sem = scraper.semester(check_driver)
                        dbase.group(group, user)
                        dbase.semester(sem, user)
                        ##### Temp for seniors #####
                        if sem not in [1, 2]:
                            page.send(sender_id, "Sorry, MIT Hodor currently just works for freshers.\nIt should soon work for the rest after sessionals (mid September)")
                            dbase.delete(user)
                        scraper.end(check_driver)
                        page.send(sender_id, responder.verified)
            except TypeError:
                print('Wrong input')

        else:
            ##################################################
            ##### HANDLE RESPONSES FROM REGISTERED USERS #####
            ##################################################

            user = User.query.filter_by(fbid = sender_id).first()
            page.typing_on(sender_id)
            resp = parser.witintent(message, wit_client)

            try:
                driver = scraper.login(user.rollno, user.password)
            except:
                driver = None
                resp = {}
                dbase.delete(user)

            ### Parsing responses begins here ###

            if 'greetings' in resp:
                page.send(sender_id, 'Hello hello!')

            if 'thanks' in resp:
                page.send(sender_id, "You're welcome!")

            if 'guardian' in resp:
                guardian_data = scraper.guardian(driver)
                response, phone = parser.guardian(resp, guardian_data)
                page.send(sender_id, str(response))
                if phone != None:
                    page.send(sender_id, fbmq.Template.Buttons("Smart response", [fbmq.Template.ButtonPhoneNumber("Call now", phone)]))

            if 'timetable' in resp:
                try:
                    timetable_data = scraper.timetable(driver)
                    response = parser.timetable(resp, timetable_data)
                    page.send(sender_id, str(response))
                except:
                    page.send(sender_id, "Encountered an error")

            if 'attendance' in resp or 'subject' in resp:
                group = user.group
                semester = user.semester
                if driver is not None:
                    attendance_data = scraper.attendance(driver, semester, group)
                    response = parser.attendance(resp, attendance_data, group)
                    print(str(response))
                    for resp in response:
                        try:
                            page.send(sender_id, str(resp))
                        except ValueError:
                            print('Faced value error {}'.format(resp))

            if 'curse' in resp:
                page.send(sender_id, message)

            if 'hodor' in resp:
                page.send(sender_id, "HODOOOOOR!")

            if 'showoff' in resp:
                page.send(sender_id, responder.features)
    
            if driver is not None:
                scraper.end(driver)

            page.send(sender_id, "Hodor!", quick_replies=quick_replies,
            metadata="DEVELOPER_DEFINED_METADATA")


if __name__ == '__main__':
    app.run(debug=True)

