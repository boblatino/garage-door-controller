import time, uuid
import smtplib
import json
import http.client
import urllib
import subprocess
import logging
from threading import Thread, get_ident
import uptime

from flask import Flask, make_response, jsonify, request, send_from_directory, abort
app = Flask(__name__)

from email.mime.text import MIMEText
from email.utils import formatdate
from email.utils import make_msgid



def mock_gpio():
    from unittest.mock import MagicMock
    global gpio
    gpio = MagicMock()
    global gpio_status
    gpio_status = 0

    gpio.input.return_value = gpio_status

def _mock_toggle():
    global gpio_status
    logging.info("FAKE TOGGLING GPIO FROM: %s" % gpio_status)
    time.sleep(3)
    if gpio_status == 0:
        gpio_status = 1
    else:
        gpio_status = 0
    
    gpio.input.return_value = gpio_status



def mock_toggle():
    from threading import Timer
    Timer(3, _mock_toggle).start()


try:
    import RPi.GPIO as gpio
except ModuleNotFoundError:
    mock_gpio()


class Door(object):
    last_action = None
    last_action_time = None
    msg_sent = False
    pb_iden = None

    def __init__(self, doorId, config):
        self.id = doorId
        self.name = config['name']
        self.relay_pin = config['relay_pin']
        self.state_pin = config['state_pin']
        self.state_pin_closed_value = config.get('state_pin_closed_value', 0)
        self.time_to_close = config.get('time_to_close', 10)
        self.time_to_open = config.get('time_to_open', 10)
        self.openhab_name = config.get('openhab_name')
        self.open_time = time.time()
        gpio.setup(self.relay_pin, gpio.OUT)
        gpio.setup(self.state_pin, gpio.IN, pull_up_down=gpio.PUD_UP)
        gpio.output(self.relay_pin, True)

    def get_state(self):
        logging.info("GPIO VAL: %s" % gpio.input(self.state_pin))
        if gpio.input(self.state_pin) == self.state_pin_closed_value:
            return 'closed'
        elif self.last_action == 'open':
            if time.time() - self.last_action_time >= self.time_to_open:
                return 'open'
            else:
                return 'opening'
        elif self.last_action ==  'close':
            if time.time() - self.last_action_time >= self.time_to_close:
                return 'open' # This state indicates a problem
            else:
                return 'closing'
        else:
            return 'open'

    def get_sensor_state(self):
        if gpio.input(self.state_pin) == self.state_pin_closed_value:
            return 'closed'
        return 'open'

    def toggle_relay(self):
        state = self.get_state()
        print(state)
        if (state == 'open'):
            self.last_action = 'close'
            self.last_action_time = time.time()
        elif state == 'closed':
            self.last_action = 'open'
            self.last_action_time = time.time()
        else:
            self.last_action = None
            self.last_action_time = None

        gpio.output(self.relay_pin, False)
        time.sleep(0.2)
        gpio.output(self.relay_pin, True)

        mock_toggle()

class Controller(object):
    def __init__(self, config):
        gpio.setwarnings(False)
        gpio.cleanup()
        gpio.setmode(gpio.BCM)
        self.config = config
        self.doors = [Door(n, c) for (n, c) in config['doors'].items()]
        for door in self.doors:
            door.last_state = 'unknown'
            door.last_state_time = time.time()

        self.use_alerts = config['config']['use_alerts']
        self.alert_type = config['alerts']['alert_type']
        self.ttw = config['alerts']['time_to_wait']
        if self.alert_type == 'smtp':
            self.use_smtp = False
            smtp_params = ("smtphost", "smtpport", "smtp_tls", "username", "password", "to_email")
            self.use_smtp = ('smtp' in config['alerts']) and set(smtp_params) <= set(config['alerts']['smtp'])
            logging.info("we are using SMTP")
        elif self.alert_type == 'pushbullet':
            self.pushbullet_access_token = config['alerts']['pushbullet']['access_token']
            logging.info("we are using Pushbullet")
        elif self.alert_type == 'pushover':
            self.pushover_user_key = config['alerts']['pushover']['user_key']
            logging.info("we are using Pushover")
        else:
            self.alert_type = None
            logging.info("No alerts configured")
        

    def status_poll(self):
        import traceback
        traceback.print_stack()
        logging.info("%s started" % get_ident())
        while self.poller_run:
            time.sleep(5)
            for door in self.doors:
                logging.info("%s Door name: %s, %d" % (get_ident(), door.name, len(self.doors)))
                new_state = door.get_state()
                logging.info("STATE: %s" % new_state)
                if (door.last_state != new_state):
                    logging.info('%s: %s => %s' % (door.name, door.last_state, new_state))
                    door.last_state = new_state
                    door.last_state_time = time.time()
                    if self.config['config']['use_openhab'] and (new_state == "open" or new_state == "closed"):
                        self.update_openhab(door.openhab_name, new_state)
                if new_state == 'open' and not door.msg_sent and time.time() - door.open_time >= self.ttw:
                    if self.use_alerts:
                        title = "%s's garage door open" % door.name
                        etime = elapsed_time(int(time.time() - door.open_time))
                        message = "%s's garage door has been open for %s" % (door.name, etime)
                        if self.alert_type == 'smtp':
                            self.send_email(title, message)
                        elif self.alert_type == 'pushbullet':
                            self.send_pushbullet(door, title, message)
                        elif self.alert_type == 'pushover':
                            self.send_pushover(door, title, message)
                        door.msg_sent = True

                if new_state == 'closed':
                    if self.use_alerts:
                        if door.msg_sent == True:
                            title = "%s's garage doors closed" % door.name
                            etime = elapsed_time(int(time.time() - door.open_time))
                            message = "%s's garage door is now closed after %s "% (door.name, etime)
                            if self.alert_type == 'smtp':
                                self.send_email(title, message)
                            elif self.alert_type == 'pushbullet':
                                self.send_pushbullet(door, title, message)
                            elif self.alert_type == 'pushover':
                                self.send_pushover(door, title, message)
                    door.open_time = time.time()
                    door.msg_sent = False

    def send_email(self, title, message):
        try:
            if self.use_smtp:
                logging.info("Sending email message")
                config = self.config['alerts']['smtp']
                
                message = MIMEText(message)
                message['Date'] = formatdate()
                message['From'] = config["username"]
                message['To'] = config["to_email"]
                message['Subject'] = config["subject"]
                message['Message-ID'] = make_msgid()
                
                server = smtplib.SMTP(config["smtphost"], config["smtpport"])
                if (config["smtp_tls"] == "True") :
                    server.starttls()
                server.login(config["username"], config["password"])
                server.sendmail(config["username"], config["to_email"], message.as_string())
                server.close()
        except Exception as inst:
            logging.info("Error sending email: " + str(inst))

    def send_pushbullet(self, door, title, message):
        try:
            logging.info("Sending pushbutton message")
            config = self.config['alerts']['pushbullet']

            if door.pb_iden != None:
                conn = httplib.HTTPSConnection("api.pushbullet.com:443")
                conn.request("DELETE", '/v2/pushes/' + door.pb_iden, "",
                             {'Authorization': 'Bearer ' + config['access_token'], 'Content-Type': 'application/json'})
                conn.getresponse()
                door.pb_iden = None

            conn = httplib.HTTPSConnection("api.pushbullet.com:443")
            conn.request("POST", "/v2/pushes",
                 json.dumps({
                     "type": "note",
                     "title": title,
                     "body": message,
                 }), {'Authorization': 'Bearer ' + config['access_token'], 'Content-Type': 'application/json'})
            response = conn.getresponse().read()
            print(response)
            door.pb_iden = json.loads(response)['iden']
        except Exception as inst:
            logging.info("Error sending to pushbullet: " + str(inst))

    def send_pushover(self, door, title, message):
        try:
            logging.info("Sending Pushover message")
            config = self.config['alerts']['pushover']
            conn = httplib.HTTPSConnection("api.pushover.net:443")
            conn.request("POST", "/1/messages.json",
                    urllib.urlencode({
                        "token": config['api_key'],
                        "user": config['user_key'],
                        "title": title,
                        "message": message,
                    }), { "Content-type": "application/x-www-form-urlencoded" })
            conn.getresponse()
        except Exception as inst:
            logging.info("Error sending to pushover: " + str(inst))

    def update_openhab(self, item, state):
        try:
            logging.info("Updating openhab")
            config = self.config['openhab']
            conn = httplib.HTTPConnection("%s:%s" % (config['server'], config['port']))
            conn.request("PUT", "/rest/items/%s/state" % item, state)
            conn.getresponse()
        except:
            logging.info("Error updating openhab: " + str(inst))

    def toggle(self, doorId):
        for d in self.doors:
            if d.id == doorId:
                logging.info('%s: toggled' % d.name)
                d.toggle_relay()
                return

    def get_config_with_default(self, config, param, default):
        if not config:
            return default
        if not param in config:
            return default
        return config[param]

    def run(self):
        if self.config['config']['use_auth']:
            pass
        else:
            pass
        
        # Start the poll timer
        logging.info("STARTING")
        self.poller_run = True
        self.poller = Thread(target=self.status_poll)
        self.poller.start()
        
        if not self.get_config_with_default(self.config['config'], 'use_https', False):
            app.run(port=self.config['site']['port'], debug=False)
        else:
            raise Exception("SSL not supported")

        self.poller_run = False
        logging.info("Waiting for background thread to stop")
        self.poller.join()
        logging.info("Bye")


def hms_string(sec_elapsed):
    h = int(sec_elapsed / (60 * 60))
    m = int((sec_elapsed % (60 * 60)) / 60)
    s = sec_elapsed % 60.
    return "{}:{:>02}:{:>05.2f}".format(h, m, s)
    

def elapsed_time(seconds, suffixes=['y','w','d','h','m','s'], add_s=False, separator=' '):
    """
    Takes an amount of seconds and turns it into a human-readable amount of time.
    """
    # the formatted time string to be returned
    time = []

    # the pieces of time to iterate over (days, hours, minutes, etc)
    # - the first piece in each tuple is the suffix (d, h, w)
    # - the second piece is the length in seconds (a day is 60s * 60m * 24h)
    parts = [(suffixes[0], 60 * 60 * 24 * 7 * 52),
             (suffixes[1], 60 * 60 * 24 * 7),
             (suffixes[2], 60 * 60 * 24),
             (suffixes[3], 60 * 60),
             (suffixes[4], 60),
             (suffixes[5], 1)]

    # for each time piece, grab the value and remaining seconds, and add it to
    # the time string
    for suffix, length in parts:
        value = seconds / length
        if value > 0:
            seconds = seconds % length
            time.append('%s%s' % (str(value),
                                  (suffix, (suffix, suffix + 's')[value > 1])[add_s]))
        if seconds < 1:
            break

    return separator.join(time)


# API

@app.route('/')
def index():
    return app.send_static_file('index.html')


@app.route('/toggle', methods=['PUT'])
def click_route():
    if not request.json:
        abort(400)

    door = request.json['door']
    for d in app.controller.doors:
        if (d.id == door):
            app.controller.toggle(door)
            return jsonify(ok="OK")

    return make_response('Not found', 404)

@app.route('/status', methods=['GET'])
def status_route():
    door = request.args.get('id')
    if door:
        for d in app.controller.doors:
            if (d.id == door):
                return jsonify(sensor_status=d.get_sensor_state(),
                            last_state=d.last_state)
    
    return make_response('Not found', 404)

@app.route('/status_all', methods=['GET'])
def status_all_route():
    res = {'doors': []}
    door = res['doors']
    for d in app.controller.doors:
        r = {}
        r['id'] = d.id
        r['name'] = d.name
        r['last_state'] = d.last_state
        r['last_state_time'] = d.last_state_time
        r['sensor_status'] = d.get_sensor_state()
        door.append(r)

    return jsonify(res)
    
@app.route('/uptime', methods=['GET'])
def uptime_route():
    return jsonify(uptime=hms_string(uptime.uptime()))


if __name__ == '__main__':
    logging.basicConfig(format='%(levelname)s:%(message)s', level=logging.DEBUG)
    with open('config.json') as config_file:
        conf = json.load(config_file)
    
    app.controller = Controller(conf)
    app.controller.run()
