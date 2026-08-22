#include <AccelStepper.h>

// CNC Shield V3, X socket
#define STEP_PIN  2
#define DIR_PIN   5
#define EN_PIN    8

AccelStepper motor(AccelStepper::DRIVER, STEP_PIN, DIR_PIN);

void setup() {
  Serial.begin(115200);
  pinMode(EN_PIN, OUTPUT);
  digitalWrite(EN_PIN, LOW);        // LOW = driver ON

  motor.setMaxSpeed(4000);
  motor.setSpeed(50);             // 1 chakkar per 1.85 s
  Serial.println("running");
}

void loop() {
  motor.runSpeed();
}
