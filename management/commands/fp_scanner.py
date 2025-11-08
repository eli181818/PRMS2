from django.core.management.base import BaseCommand
from django.utils import timezone
import serial
import time
import requests
from api.models import Patient, VitalSigns, QueueEntry
from api.utils import compute_patient_priority

class Command(BaseCommand):
    help = 'Reads fingerprint data from Arduino AS608 sensor'

    def add_arguments(self, parser):
        parser.add_argument(
            '--port',
            type=str,
            default='/dev/ttyS0',  # RPi serial port
            help='Serial port (default: /dev/ttyS0 for RPi)'
        )
        parser.add_argument(
            '--baud',
            type=int,
            default=9600,
            help='Baud rate (default: 9600)'
        )
        parser.add_argument(
            '--mode',
            type=str,
            default='verify',
            choices=['verify', 'enroll'],
            help='Scanner mode: verify or enroll (default: verify)'
        )

    def handle(self, *args, **options):
        port = options['port']
        baud = options['baud']
        mode = options['mode']
        
        self.stdout.write(self.style.SUCCESS(f'Starting fingerprint scanner on {port}...'))
        self.stdout.write(self.style.SUCCESS(f'Mode: {mode}'))
        
        try:
            ser = serial.Serial(port, baud, timeout=1)
            time.sleep(2)  # Wait for Arduino to reset
            
            self.stdout.write(self.style.SUCCESS('Connected to Arduino'))
            
            # Read startup messages from Arduino
            time.sleep(1)
            while ser.in_waiting > 0:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                if line:
                    self.stdout.write(f'Arduino: {line}')
            
            if mode == 'verify':
                self.run_verify_mode(ser)
            elif mode == 'enroll':
                self.run_enroll_mode(ser)
                
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING('\nStopping fingerprint scanner...'))
            ser.close()
            
        except Exception as e:
            self.stderr.write(self.style.ERROR(f'Error: {e}'))

    def run_verify_mode(self, ser):
        """Continuous verification mode - automatically scan for fingerprints"""
        self.stdout.write(self.style.SUCCESS('Verification mode active. Place finger on sensor...'))
        
        last_scan_time = 0
        
        while True:
            try:
                # Automatically trigger scan every 2 seconds
                current_time = time.time()
                if current_time - last_scan_time > 2:
                    ser.write(b"VERIFY\n")
                    last_scan_time = current_time
                
                if ser.in_waiting > 0:
                    line = ser.readline().decode('utf-8', errors='ignore').strip()
                    
                    if line:
                        self.stdout.write(f'Arduino: {line}')
                        self.process_verify_response(line)
                        
                time.sleep(0.1)
                        
            except Exception as e:
                self.stderr.write(self.style.ERROR(f'Error in verify loop: {e}'))
                time.sleep(1)

    def run_enroll_mode(self, ser):
        """Enroll mode - manual enrollment with patient ID"""
        self.stdout.write(self.style.WARNING('Enroll mode'))
        self.stdout.write('This mode requires manual patient ID input.')
        
        while True:
            try:
                patient_id = input('\nEnter Patient ID to enroll (or "exit" to quit): ').strip()
                
                if patient_id.lower() == 'exit':
                    break
                
                # Check if patient exists
                try:
                    patient = Patient.objects.get(patient_id=patient_id)
                    self.stdout.write(self.style.SUCCESS(
                        f'Found patient: {patient.first_name} {patient.last_name}'
                    ))
                    
                    # Check if patient already has fingerprint
                    if patient.fingerprint_id:
                        response = input(f'Patient already has fingerprint ID {patient.fingerprint_id}. Overwrite? (yes/no): ')
                        if response.lower() != 'yes':
                            continue
                    
                    # Send enroll command to Arduino
                    self.stdout.write('Sending enroll command to Arduino...')
                    ser.write(b"ENROLL\n")
                    
                    # Listen for enrollment process
                    enrollment_complete = False
                    fingerprint_id = None
                    
                    while not enrollment_complete:
                        if ser.in_waiting > 0:
                            line = ser.readline().decode('utf-8', errors='ignore').strip()
                            if line:
                                self.stdout.write(f'Arduino: {line}')
                                
                                if line.startswith('ENROLL:SUCCESS:'):
                                    fingerprint_id = line.split(':')[2]
                                    enrollment_complete = True
                                    
                                    # Update patient record
                                    patient.fingerprint_id = fingerprint_id
                                    patient.save()
                                    
                                    self.stdout.write(self.style.SUCCESS(
                                        f'Fingerprint enrolled! ID: {fingerprint_id}'
                                    ))
                                    self.stdout.write(self.style.SUCCESS(
                                        f'Patient {patient_id} updated with fingerprint ID {fingerprint_id}'
                                    ))
                                    
                                elif 'ERROR' in line:
                                    self.stderr.write(self.style.ERROR('Enrollment failed!'))
                                    enrollment_complete = True
                        
                        time.sleep(0.1)
                        
                except Patient.DoesNotExist:
                    self.stderr.write(self.style.ERROR(f'Patient {patient_id} not found!'))
                    
            except Exception as e:
                self.stderr.write(self.style.ERROR(f'Error in enroll mode: {e}'))

    def process_verify_response(self, data):
        """Process verification response from Arduino"""
        try:
            if data.startswith('MATCH:'):
                # Format: MATCH:fingerprint_id:confidence
                parts = data.split(':')
                if len(parts) >= 3:
                    fingerprint_id = parts[1]
                    confidence = parts[2]
                    
                    self.stdout.write(self.style.SUCCESS(
                        f'Fingerprint matched! ID: {fingerprint_id}, Confidence: {confidence}'
                    ))
                    
                    # Find patient with this fingerprint
                    try:
                        patient = Patient.objects.get(fingerprint_id=fingerprint_id)
                        
                        # Update last visit
                        patient.last_visit = timezone.now()
                        patient.save()
                        
                        self.stdout.write(self.style.SUCCESS(
                            f'✓ Patient identified: {patient.first_name} {patient.last_name} ({patient.patient_id})'
                        ))
                        
                        # Check if patient needs to be added to queue
                        self.check_and_add_to_queue(patient, confidence)
                        
                    except Patient.DoesNotExist:
                        self.stdout.write(self.style.WARNING(
                            f'⚠ Fingerprint ID {fingerprint_id} not linked to any patient!'
                        ))
            
            elif data == 'VERIFY:NOT_FOUND':
                self.stdout.write(self.style.WARNING('⚠ Fingerprint not recognized'))
            
            elif 'ERROR' in data:
                self.stderr.write(self.style.ERROR(f'Arduino error: {data}'))
                
        except Exception as e:
            self.stderr.write(self.style.ERROR(f'Processing error: {e}'))

    def check_and_add_to_queue(self, patient, confidence):
        """Check if patient has complete vitals and add to queue if needed"""
        try:
            # Get today's vitals
            today = timezone.now().date()
            today_start = timezone.make_aware(
                timezone.datetime.combine(today, timezone.datetime.min.time())
            )
            today_end = timezone.make_aware(
                timezone.datetime.combine(today, timezone.datetime.max.time())
            )
            
            latest_vitals = VitalSigns.objects.filter(
                patient=patient,
                date_time_recorded__range=(today_start, today_end)
            ).order_by('-date_time_recorded').first()
            
            if latest_vitals:
                # Check if all vitals are complete
                all_vitals_complete = all([
                    latest_vitals.blood_pressure,
                    latest_vitals.heart_rate,
                    latest_vitals.temperature,
                    latest_vitals.oxygen_saturation,
                    latest_vitals.weight,
                    latest_vitals.height,
                ])
                
                if all_vitals_complete:
                    # Check if already in queue
                    existing_queue = QueueEntry.objects.filter(patient=patient).first()
                    
                    if not existing_queue:
                        # Calculate priority
                        priority = compute_patient_priority(patient)
                        
                        # Add to queue
                        queue_entry = QueueEntry.objects.create(
                            patient=patient,
                            priority=priority,
                            entered_at=timezone.now()
                        )
                        
                        self.stdout.write(self.style.SUCCESS(
                            f'✓ Added to queue with priority: {priority} (Queue #: {queue_entry.queue_number})'
                        ))
                    else:
                        self.stdout.write(self.style.WARNING(
                            f'ℹ Patient already in queue (#{existing_queue.queue_number})'
                        ))
                else:
                    self.stdout.write(self.style.WARNING(
                        'ℹ Patient needs to complete vital signs before joining queue'
                    ))
            else:
                self.stdout.write(self.style.WARNING(
                    'ℹ No vital signs recorded today. Patient needs vitals check.'
                ))
                
        except Exception as e:
            self.stderr.write(self.style.ERROR(f'Queue check error: {e}'))