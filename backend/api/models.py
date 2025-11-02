from django.db import models
from django.utils import timezone
from datetime import date
from .utils import compute_patient_priority
from django.contrib.auth.hashers import make_password, check_password

class HCStaff(models.Model):
    name = models.CharField(max_length=50)
    staff_pin = models.CharField(max_length=255, unique=True)
    
    def set_pin(self, raw_pin):
        """Hashes and sets the staff PIN."""
        self.staff_pin = make_password(raw_pin)

    def check_pin(self, raw_pin):
        """Verifies a raw PIN against the stored hash."""
        return check_password(raw_pin, self.staff_pin)

    def save(self, *args, **kwargs):
        # Hash the PIN if it’s not already hashed
        if self.staff_pin and not self.staff_pin.startswith('pbkdf2_'):
            self.staff_pin = make_password(self.staff_pin)
        super().save(*args, **kwargs)

class Patient(models.Model):    
    patient_id = models.CharField(max_length=15, unique=True, primary_key=True)
    first_name = models.CharField(max_length=50)
    middle_initial = models.CharField(max_length=50, null=True, blank=True)
    last_name = models.CharField(max_length=50)
    sex = models.CharField(max_length=6, choices=[('Male', 'Male'), ('Female', 'Female')])
    contact = models.CharField(max_length=11, default='N/A')
    address = models.TextField(max_length=300)
    username = models.CharField(max_length=20, null=True, blank=True, unique=True)
    birthdate = models.DateField(null=True, blank=True)
    pin = models.CharField(max_length=255)
    fingerprint_id = models.CharField(max_length=4, null=True, blank=True, unique=True)
    last_visit = models.DateTimeField(null=True, blank=True)
    
    def set_pin(self, raw_pin):
        self.pin = make_password(raw_pin)

    def check_pin(self, raw_pin):
        return check_password(raw_pin, self.pin)
    
    @property
    def age(self):
        """Calculates age based on birthdate and today's date."""
        if self.birthdate:
            today = date.today()
            # Calculate the age: today.year - birthdate.year
            # Subtract 1 if the current date is before the birthday this year
            return today.year - self.birthdate.year - ((today.month, today.day) < (self.birthdate.month, self.birthdate.day))
        return None

    def save(self, *args, **kwargs):  
        if not self.patient_id:
            today = timezone.now().date()
            yyyymmdd = today.strftime("%Y%m%d")
            count_today = Patient.objects.filter(patient_id__startswith=f"P-{yyyymmdd}").count() + 1
            self.patient_id = f"P-{yyyymmdd}-{count_today:03d}"
            pass
        
        # Hash the PIN if it's not already hashed
        if self.pin and not self.pin.startswith('pbkdf2_'):
            self.pin = make_password(self.pin)
            
          # set last_visit on first creation
        if not self.last_visit:
            self.last_visit = timezone.now()
            super().save(*args, **kwargs)

    def is_senior(self):
        """Helper: Check if patient is senior (age >= 65)."""
        return self.age is not None and self.age >= 65

class VitalSigns(models.Model):
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name='vital_signs')  # Link to Patient model
    device_id = models.CharField(max_length=50, null=True, blank=True)  # Link to the RPi device
    date_time_recorded = models.DateTimeField(auto_now_add=True)
    heart_rate = models.IntegerField(null=True, blank=True)  # bpm; Allow null for optional
    temperature = models.FloatField(null=True, blank=True)  # °C
    oxygen_saturation = models.FloatField(null=True, blank=True)  # %
    # Optional: Add BP if needed (as per your original query)
    blood_pressure = models.IntegerField(null=True, blank=True)  # mmHg
    # blood_pressure_diastolic = models.IntegerField(null=True, blank=True)  # mmHg
    height = models.FloatField(null=True, blank=True)  # meters
    weight = models.FloatField(null=True, blank=True)  # kg
    bmi = models.FloatField(null=True, blank=True) 
    
    def save(self, *args, **kwargs):  # Fixed: Override save() to auto-compute BMI
        # Compute BMI if height and weight are provided
        if self.height and self.weight and self.height > 0:
            self.BMI = round(self.weight / (self.height ** 2), 2)
        # If no height/weight, leave BMI as None
        super().save(*args, **kwargs)

class QueueEntry(models.Model):
    PRIORITY_CHOICES = [
        ('CRITICAL', 'Critical'),
        ('HIGH', 'High'),
        ('MEDIUM', 'Medium'),
        ('NORMAL', 'Normal'),
    ]
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name='queue_entries')
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, null=True, blank=True)
    entered_at = models.DateTimeField(default=timezone.now)
    queue_number = models.CharField(max_length=10, null=True, blank=True)
    
    class Meta:
        ordering = ['-entered_at']
    
    def save(self, *args, **kwargs):
        # UPDATE LAST VISIT when entering queue
        self.patient.last_visit = timezone.now()
        self.patient.save()
        
        # Auto-compute priority on save (if not set)
        if not self.priority:
            self.priority = compute_patient_priority(self.patient)
        
        if not self.queue_number:
            today = timezone.now().date()
            count_today = QueueEntry.objects.filter(entered_at__date=today).count() + 1
            self.queue_number = f"Q{count_today:03d}"
        super().save(*args, **kwargs)

