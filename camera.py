'''"""import cv2
import dlib
import numpy as np
import sqlite3
import os
import time
import threading
from scipy.spatial import distance as dist
from datetime import datetime
from pygame import mixer
from flask_mail import Message

# ----------------------------
# BASE PATH
# ----------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCREENSHOT_DIR = os.path.join(BASE_DIR, "screenshots")

if not os.path.exists(SCREENSHOT_DIR):
    os.makedirs(SCREENSHOT_DIR)

# ----------------------------
# LOAD SOUND
# ----------------------------
mixer.init()
mixer.music.load(os.path.join(BASE_DIR, "music.wav"))

# ----------------------------
# FACE DETECTOR
# ----------------------------
detector = dlib.get_frontal_face_detector()
predictor = dlib.shape_predictor(
    os.path.join(BASE_DIR, "model/shape_predictor_68_face_landmarks.dat")
)

# ----------------------------
# CONSTANTS (STABLE VALUES)
# ----------------------------
LEFT_EYE = list(range(42, 48))
RIGHT_EYE = list(range(36, 42))

EYE_AR_THRESH = 0.27              # Lowered threshold
EYE_AR_CONSEC_FRAMES = 15         # More frames required

DISTRACTION_TIME = 2              # 2 seconds continuous
EMAIL_INTERVAL = 60               # 1 min cooldown

COUNTER = 0
ALARM_ON = False
last_email_time = 0

distraction_start = None
side_start = None
head_drop_start = None


# ----------------------------
# EAR CALCULATION
# ----------------------------
def eye_aspect_ratio(eye):
    A = dist.euclidean(eye[1], eye[5])
    B = dist.euclidean(eye[2], eye[4])
    C = dist.euclidean(eye[0], eye[3])
    return (A + B) / (2.0 * C)


# ----------------------------
# SAVE LOG
# ----------------------------
def save_log(username, status):
    conn = sqlite3.connect("drowsiness.db")
    c = conn.cursor()
    c.execute("""
        INSERT INTO logs(username,date_time,status)
        VALUES(?,?,?)
    """, (username, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), status))
    conn.commit()
    conn.close()


# ----------------------------
# BACKGROUND EMAIL THREAD
# ----------------------------
def send_email_background(username, status, mail, app, screenshot_path=None):
    def task():
        send_alert_email(username, status, mail, app, screenshot_path)
    threading.Thread(target=task, daemon=True).start()


# ----------------------------
# SEND EMAIL ALERT
# ----------------------------
def send_alert_email(username, status, mail, app, screenshot_path=None):
    global last_email_time

    # Cooldown to avoid spam
    if time.time() - last_email_time < EMAIL_INTERVAL:
        return

    last_email_time = time.time()

    conn = sqlite3.connect("drowsiness.db")
    c = conn.cursor()

    c.execute("SELECT email FROM users WHERE role IN ('admin','superadmin')")
    admins = c.fetchall()

    c.execute("SELECT email FROM users WHERE username=?", (username,))
    user_email = c.fetchone()

    conn.close()

    recipients = [a[0] for a in admins]
    if user_email:
        recipients.append(user_email[0])

    if not recipients:
        return

    msg = Message(
        subject="🚨 Driver Safety Alert",
        sender=app.config['MAIL_USERNAME'],
        recipients=recipients
    )

    msg.body = f"""
ALERT TYPE: {status}
USER: {username}
TIME: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
"""

    if screenshot_path and os.path.exists(screenshot_path):
        with open(screenshot_path, "rb") as f:
            msg.attach(
                filename=os.path.basename(screenshot_path),
                content_type="image/jpeg",
                data=f.read()
            )

    with app.app_context():
        mail.send(msg)


# ----------------------------
# MAIN GENERATOR
# ----------------------------
def generate_frames(username, mail, app):
    global COUNTER, ALARM_ON, distraction_start, alert_lock

    cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)

    while True:
        success, frame = cap.read()
        if not success:
            break

        frame = cv2.resize(frame, (640, 480))
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        faces = detector(gray)
        status_text = "Normal"

        # ---------------- NO FACE ----------------
        if len(faces) == 0:
            status_text = "NO FACE DETECTED!"
            COUNTER = 0
            ALARM_ON = False

        for face in faces:

            shape = predictor(gray, face)
            coords = np.zeros((68, 2), dtype="int")

            for i in range(68):
                coords[i] = (shape.part(i).x, shape.part(i).y)

            leftEye = coords[LEFT_EYE]
            rightEye = coords[RIGHT_EYE]

            ear = (eye_aspect_ratio(leftEye) + eye_aspect_ratio(rightEye)) / 2.0

            # Display EAR value
            cv2.putText(frame, f"EAR: {round(ear,3)}",
                        (10, 60),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.7, (0, 255, 255), 2)

            # ---------------- DROWSINESS ----------------
            if ear < EYE_AR_THRESH:
                COUNTER += 1
            else:
                COUNTER = max(0, COUNTER - 1)

            if COUNTER >= EYE_AR_CONSEC_FRAMES and not ALARM_ON:
                ALARM_ON = True
                status_text = "DROWSINESS ALERT!"
                mixer.music.play()

                save_log(username, "Drowsiness Detected")

                screenshot_path = os.path.join(
                    SCREENSHOT_DIR,
                    f"{username}_{int(time.time())}.jpg"
                )
                cv2.imwrite(screenshot_path, frame)

                send_email_background(
                    username,
                    "Drowsiness Detected",
                    mail,
                    app,
                    screenshot_path
                )

            if ear >= EYE_AR_THRESH and ALARM_ON:
                ALARM_ON = False
                mixer.music.stop()

            # ---------------- DISTRACTION (STABLE) ----------------
            left_face = coords[0]
            right_face = coords[16]
            nose = coords[30]
            chin = coords[8]
            forehead = coords[27]

            face_center_x = (left_face[0] + right_face[0]) // 2
            face_width = right_face[0] - left_face[0]

            horizontal_shift = abs(nose[0] - face_center_x)

            HORIZONTAL_LIMIT = 0.22 * face_width
            print("Horizontal shift:", horizontal_shift)
            print("Limit:", HORIZONTAL_LIMIT)
            cv2.putText(frame, f"Shift: {int(horizontal_shift)}",
            (10, 90),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6, (255, 255, 0), 2)

            cv2.putText(frame, f"Limit: {int(HORIZONTAL_LIMIT)}",
            (10, 115),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6, (0, 255, 255), 2)

            if horizontal_shift > HORIZONTAL_LIMIT:
                if side_start is None:
                    side_start = time.time()
                elapsed = time.time() - side_start

                if elapsed > 2:   # wait 2 seconds
                    status_text = "LOOKING SIDE!"
                    save_log(username, "Looking Side Detected")
            else:
                side_start = None
        

            # ---------------- HEAD DROP (LESS SENSITIVE) ----------------
            face_height = chin[1] - forehead[1]

            if chin[1] - nose[1] > (0.75 * face_height):
                if head_drop_start is None:
                    head_drop_start = time.time()
                elif time.time() - head_drop_start > 3:  # wait 3 sec
                    status_text = "HEAD DROPPING!"
                    save_log(username, "Head Drop Detected")
            else:
                head_drop_start = None

        cv2.putText(frame, f"Status: {status_text}",
                    (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7, (0, 0, 255), 2)

        ret, buffer = cv2.imencode(".jpg", frame)
        frame = buffer.tobytes()

        yield (b"--frame\r\n"
               b"Content-Type: image/jpeg\r\n\r\n" +
               frame + b"\r\n")

    cap.release()"""'''

'''import cv2
import dlib
import numpy as np
import sqlite3
import os
import time
import threading
from scipy.spatial import distance as dist
from datetime import datetime
from pygame import mixer
from flask_mail import Message
from ultralytics import YOLO

# ---------------- BASE PATH ----------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCREENSHOT_DIR = os.path.join(BASE_DIR, "screenshots")

if not os.path.exists(SCREENSHOT_DIR):
    os.makedirs(SCREENSHOT_DIR)

# ---------------- LOAD SOUND ----------------
mixer.init()
mixer.music.load(os.path.join(BASE_DIR, "music.wav"))

# ---------------- FACE DETECTOR ----------------
detector = dlib.get_frontal_face_detector()
predictor = dlib.shape_predictor(
    os.path.join(BASE_DIR, "model/shape_predictor_68_face_landmarks.dat")
)

# ---------------- YOLO MODEL ----------------
yolo_model = YOLO("yolov8n.pt")

# ---------------- CONSTANTS ----------------
LEFT_EYE = list(range(42, 48))
RIGHT_EYE = list(range(36, 42))

EYE_AR_THRESH = 0.27
EYE_AR_CONSEC_FRAMES = 15

SIDE_TIME = 2
HEAD_DROP_TIME = 3
PHONE_TIME = 2
EMAIL_INTERVAL = 60

# ---------------- GLOBAL STATES ----------------
COUNTER = 0
ALARM_ON = False
last_email_time = 0

side_start = None
head_drop_start = None
phone_start = None

frame_count = 0
phone_boxes = []


# ---------------- EAR CALCULATION ----------------
def eye_aspect_ratio(eye):
    A = dist.euclidean(eye[1], eye[5])
    B = dist.euclidean(eye[2], eye[4])
    C = dist.euclidean(eye[0], eye[3])
    return (A + B) / (2.0 * C)


# ---------------- SAVE LOG ----------------
def save_log(username, status):
    conn = sqlite3.connect("drowsiness.db")
    c = conn.cursor()
    c.execute("""
        INSERT INTO logs(username,date_time,status)
        VALUES(?,?,?)
    """, (username, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), status))
    conn.commit()
    conn.close()


# ---------------- EMAIL THREAD ----------------
def send_email_background(username, status, mail, app, screenshot_path=None):
    def task():
        send_alert_email(username, status, mail, app, screenshot_path)
    threading.Thread(target=task, daemon=True).start()


# ---------------- SEND EMAIL ----------------
def send_alert_email(username, status, mail, app, screenshot_path=None):
    global last_email_time

    if time.time() - last_email_time < EMAIL_INTERVAL:
        return

    last_email_time = time.time()

    conn = sqlite3.connect("drowsiness.db")
    c = conn.cursor()

    c.execute("SELECT email FROM users WHERE role IN ('admin','superadmin')")
    admins = c.fetchall()

    c.execute("SELECT email FROM users WHERE username=?", (username,))
    user_email = c.fetchone()

    conn.close()

    recipients = [a[0] for a in admins]
    if user_email:
        recipients.append(user_email[0])

    if not recipients:
        return

    msg = Message(
        subject="🚨 Driver Safety Alert",
        sender=app.config['MAIL_USERNAME'],
        recipients=recipients
    )

    msg.body = f"""
ALERT TYPE: {status}
USER: {username}
TIME: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
"""

    if screenshot_path and os.path.exists(screenshot_path):
        with open(screenshot_path, "rb") as f:
            msg.attach(
                filename=os.path.basename(screenshot_path),
                content_type="image/jpeg",
                data=f.read()
            )

    with app.app_context():
        mail.send(msg)


# ---------------- MAIN GENERATOR ----------------
def generate_frames(username, mail, app):

    global COUNTER, ALARM_ON
    global side_start, head_drop_start, phone_start
    global frame_count, phone_boxes

    cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)

    while True:
        success, frame = cap.read()
        if not success:
            break

        frame_count += 1
        frame = cv2.resize(frame, (640, 480))
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        faces = detector(gray)
        status_text = "Normal"

        # ---------------- YOLO PHONE DETECTION (EVERY 10 FRAMES) ----------------
        if frame_count % 10 == 0:
            phone_boxes = []
            results = yolo_model(frame, verbose=False)

            for r in results:
                for box in r.boxes:
                    cls = int(box.cls[0])
                    label = yolo_model.names[cls]

                    if label == "cell phone":
                        x1, y1, x2, y2 = map(int, box.xyxy[0])
                        phone_boxes.append((x1, y1, x2, y2))

        # Draw phone boxes
        for (x1, y1, x2, y2) in phone_boxes:
            cv2.rectangle(frame, (x1,y1),(x2,y2),(0,255,255),2)
            cv2.putText(frame,"PHONE",(x1,y1-10),
                        cv2.FONT_HERSHEY_SIMPLEX,0.6,(0,255,255),2)

        # ---------------- NO FACE ----------------
        if len(faces) == 0:
            status_text = "NO FACE DETECTED!"
            COUNTER = 0
            ALARM_ON = False

        for face in faces:

            shape = predictor(gray, face)
            coords = np.zeros((68, 2), dtype="int")

            for i in range(68):
                coords[i] = (shape.part(i).x, shape.part(i).y)

            # ---------------- DROWSINESS ----------------
            leftEye = coords[LEFT_EYE]
            rightEye = coords[RIGHT_EYE]

            ear = (eye_aspect_ratio(leftEye) + eye_aspect_ratio(rightEye)) / 2.0

            cv2.putText(frame, f"EAR: {round(ear,3)}",
                        (10,60),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.7,(0,255,255),2)

            if ear < EYE_AR_THRESH:
                COUNTER += 1
            else:
                COUNTER = max(0, COUNTER - 1)

            if COUNTER >= EYE_AR_CONSEC_FRAMES and not ALARM_ON:

                ALARM_ON = True
                status_text = "DROWSINESS ALERT!"
                mixer.music.play()

                save_log(username,"Drowsiness Detected")

                screenshot_path = os.path.join(
                    SCREENSHOT_DIR,
                    f"{username}_{int(time.time())}.jpg"
                )
                cv2.imwrite(screenshot_path,frame)

                send_email_background(
                    username,
                    "Drowsiness Detected",
                    mail,
                    app,
                    screenshot_path
                )

            if ear >= EYE_AR_THRESH and ALARM_ON:
                ALARM_ON = False
                mixer.music.stop()

            # ---------------- LOOKING SIDE ----------------
            left_face = coords[0]
            right_face = coords[16]
            nose = coords[30]
            chin = coords[8]
            forehead = coords[27]

            face_center_x = (left_face[0] + right_face[0]) // 2
            face_width = right_face[0] - left_face[0]

            horizontal_shift = abs(nose[0] - face_center_x)
            HORIZONTAL_LIMIT = 0.22 * face_width

            if horizontal_shift > HORIZONTAL_LIMIT:

                if side_start is None:
                    side_start = time.time()

                if time.time() - side_start > SIDE_TIME:
                    status_text = "LOOKING SIDE!"
                    save_log(username,"Looking Side Detected")

            else:
                side_start = None

            # ---------------- HEAD DROP ----------------
            face_height = chin[1] - forehead[1]

            if chin[1] - nose[1] > (0.75 * face_height):

                if head_drop_start is None:
                    head_drop_start = time.time()

                if time.time() - head_drop_start > HEAD_DROP_TIME:
                    status_text = "HEAD DROPPING!"
                    save_log(username,"Head Drop Detected")

            else:
                head_drop_start = None

            # ---------------- PHONE NEAR EAR ----------------
            left_ear = coords[2]
            right_ear = coords[14]

            for (x1,y1,x2,y2) in phone_boxes:

                phone_center_x = (x1 + x2) // 2

                if abs(phone_center_x - left_ear[0]) < 80 or \
                   abs(phone_center_x - right_ear[0]) < 80:

                    if phone_start is None:
                        phone_start = time.time()

                    if time.time() - phone_start > PHONE_TIME:

                        status_text = "PHONE NEAR EAR!"
                        mixer.music.play()

                        save_log(username,"Phone Near Ear Detected")

                else:
                    phone_start = None

        cv2.putText(frame,f"Status: {status_text}",
                    (10,30),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7,(0,0,255),2)

        ret, buffer = cv2.imencode(".jpg",frame)
        frame = buffer.tobytes()

        yield (b"--frame\r\n"
               b"Content-Type: image/jpeg\r\n\r\n" +
               frame + b"\r\n")

    cap.release()'''

import cv2
import dlib
import numpy as np
import sqlite3
import os
import time
import threading
from scipy.spatial import distance as dist
from datetime import datetime
from pygame import mixer
from flask_mail import Message
from ultralytics import YOLO

# ---------------- BASE PATH ----------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCREENSHOT_DIR = os.path.join(BASE_DIR, "screenshots")

if not os.path.exists(SCREENSHOT_DIR):
    os.makedirs(SCREENSHOT_DIR)

# ---------------- SOUND ----------------
mixer.init()
mixer.music.load(os.path.join(BASE_DIR, "music.wav"))

# ---------------- MODELS ----------------
detector = dlib.get_frontal_face_detector()

predictor = dlib.shape_predictor(
    os.path.join(BASE_DIR,"model/shape_predictor_68_face_landmarks.dat")
)

yolo_model = YOLO("yolov8n.pt")

# ---------------- CONSTANTS ----------------
LEFT_EYE = list(range(42,48))
RIGHT_EYE = list(range(36,42))
MOUTH = list(range(60,68))

EYE_AR_THRESH = 0.22
EYE_AR_CONSEC_FRAMES = 20

MAR_THRESH = 0.75

SIDE_TIME = 2
HEAD_TIME = 3
PHONE_TIME = 2

EMAIL_INTERVAL = 60

# ---------------- GLOBAL STATES ----------------
COUNTER = 0
ALARM_ON = False
last_email_time = 0

side_start = None
head_start = None
phone_start = None
yawn_start = None

frame_count = 0
phone_boxes = []

# ---------------- EAR ----------------
def eye_aspect_ratio(eye):

    A = dist.euclidean(eye[1],eye[5])
    B = dist.euclidean(eye[2],eye[4])
    C = dist.euclidean(eye[0],eye[3])

    return (A+B)/(2.0*C)

# ---------------- MAR ----------------
def mouth_aspect_ratio(mouth):

    A = dist.euclidean(mouth[2],mouth[6])
    B = dist.euclidean(mouth[3],mouth[5])
    C = dist.euclidean(mouth[0],mouth[4])

    return (A+B)/(2.0*C)

# ---------------- SAVE LOG ----------------
def save_log(username,status):

    conn = sqlite3.connect("drowsiness.db")
    c = conn.cursor()

    c.execute("""
    INSERT INTO logs(username,date_time,status)
    VALUES(?,?,?)
    """,(username,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        status))

    conn.commit()
    conn.close()

# ---------------- EMAIL ----------------
def send_email_background(username,status,mail,app):

    def task():

        conn = sqlite3.connect("drowsiness.db")
        c = conn.cursor()

        c.execute("SELECT email FROM users WHERE role IN ('admin','superadmin')")
        admins = c.fetchall()

        conn.close()

        recipients=[a[0] for a in admins]

        msg = Message(
            subject="Driver Alert",
            sender=app.config['MAIL_USERNAME'],
            recipients=recipients
        )

        msg.body=f"""
User : {username}
Alert : {status}
Time : {datetime.now()}
"""

        with app.app_context():
            mail.send(msg)

    threading.Thread(target=task,daemon=True).start()

# ---------------- MAIN ----------------
def generate_frames(username,mail,app):

    global COUNTER,ALARM_ON
    global side_start,head_start,phone_start,yawn_start
    global frame_count,phone_boxes

    cap = cv2.VideoCapture(0)

    while True:

        success,frame = cap.read()

        if not success:
            break

        frame = cv2.resize(frame,(640,480))
        gray = cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)

        faces = detector(gray)

        status="Normal"

        # ---------- YOLO PHONE ----------
        frame_count +=1

        if frame_count%12==0:

            phone_boxes=[]

            results = yolo_model(frame,verbose=False)

            for r in results:

                for box in r.boxes:

                    cls=int(box.cls[0])
                    label=yolo_model.names[cls]

                    if label=="cell phone":

                        x1,y1,x2,y2=map(int,box.xyxy[0])
                        phone_boxes.append((x1,y1,x2,y2))

        for (x1,y1,x2,y2) in phone_boxes:

            cv2.rectangle(frame,(x1,y1),(x2,y2),(0,255,255),2)
            cv2.putText(frame,"PHONE",(x1,y1-10),
                        cv2.FONT_HERSHEY_SIMPLEX,0.6,(0,255,255),2)

        if len(faces)==0:

            status="NO FACE"

        for face in faces:

            shape=predictor(gray,face)

            coords=np.zeros((68,2),dtype="int")

            for i in range(68):
                coords[i]=(shape.part(i).x,shape.part(i).y)

            # ---------- DROWSINESS ----------
            leftEye=coords[LEFT_EYE]
            rightEye=coords[RIGHT_EYE]

            ear=(eye_aspect_ratio(leftEye)+eye_aspect_ratio(rightEye))/2.0

            cv2.putText(frame,f"EAR:{round(ear,2)}",(10,60),
                        cv2.FONT_HERSHEY_SIMPLEX,0.7,(0,255,255),2)

            if ear<EYE_AR_THRESH:

                COUNTER+=1

            else:

                COUNTER=0

            if COUNTER>=EYE_AR_CONSEC_FRAMES:

                status="DROWSINESS"

                if not ALARM_ON:

                    ALARM_ON=True
                    mixer.music.play()

                    save_log(username,"Drowsiness")
                    send_email_background(username,"Drowsiness",mail,app)

            else:

                if ALARM_ON:

                    mixer.music.stop()
                    ALARM_ON=False

            # ---------- YAWNING ----------
            mouth=coords[MOUTH]
            mar=mouth_aspect_ratio(mouth)

            if mar>MAR_THRESH:

                if yawn_start is None:
                    yawn_start=time.time()

                if time.time()-yawn_start>2:

                    status="YAWNING"

            else:

                yawn_start=None

            # ---------- LOOKING SIDE ----------
            left_face=coords[0]
            right_face=coords[16]
            nose=coords[30]

            left_dist=abs(nose[0]-left_face[0])
            right_dist=abs(right_face[0]-nose[0])

            ratio=left_dist/right_dist if right_dist!=0 else 1

            if ratio>1.6 or ratio<0.6:

                if side_start is None:
                    side_start=time.time()

                if time.time()-side_start>SIDE_TIME:

                    status="LOOKING SIDE"

                    if not ALARM_ON:
                        mixer.music.play()
                        ALARM_ON=True

                        save_log(username,"Looking Side")
                        send_email_background(username,"Looking Side",mail,app)

            else:

                side_start=None

            # ---------- HEAD DROP ----------
            chin=coords[8]
            forehead=coords[27]

            face_height=chin[1]-forehead[1]

            if (nose[1]-forehead[1])/face_height>0.65:

                if head_start is None:
                    head_start=time.time()

                if time.time()-head_start>HEAD_TIME:

                    status="HEAD DROP"

            else:

                head_start=None

            # ---------- PHONE ----------
            left_ear=coords[2]
            right_ear=coords[14]

            for (x1,y1,x2,y2) in phone_boxes:

                phone_x=(x1+x2)//2

                if abs(phone_x-left_ear[0])<80 or abs(phone_x-right_ear[0])<80:

                    if phone_start is None:
                        phone_start=time.time()

                    if time.time()-phone_start>PHONE_TIME:

                        status="PHONE USE"

                        if not ALARM_ON:
                            mixer.music.play()
                            ALARM_ON=True

                            save_log(username,"Phone Use")
                            send_email_background(username,"Phone Use",mail,app)

                else:

                    phone_start=None
        
        if status == "Normal" and ALARM_ON:
            mixer.music.stop()
            ALARM_ON = False
        cv2.putText(frame,f"Status:{status}",
                    (10,30),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7,(0,0,255),2)

        ret,buffer=cv2.imencode(".jpg",frame)
        frame=buffer.tobytes()

        yield(b'--frame\r\n'
              b'Content-Type:image/jpeg\r\n\r\n'+frame+b'\r\n')

    cap.release()