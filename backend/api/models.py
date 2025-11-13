from django.db import models, IntegrityError, transaction
from django.utils import timezone
from datetime import date
from .utils import compute_patient_priority
from django.contrib.auth.hashers import make_password, check_password

class HCStaff(models.Model):
    staff_id = models.AutoField(primary_key=True)
    username = models.CharField(max_length=20, default='staff')
    staff_pin = models.CharField(max_length=255, unique=True)
    
    def set_pin(self, raw_pin):
        """Hashes and sets the staff PIN."""
        self.staff_pin = make_password(raw_pin)

    def check_pin(self, raw_pin):
        """Verifies a raw PIN against the stored hash."""
        return check_password(raw_pin, self.staff_pin)

    def save(self, *args, **kwargs):
        if self.staff_pin and not self.staff_pin.startswith('pbkdf2_'):
            self.staff_pin = make_password(self.staff_pin)
        super().save(*args, **kwargs)


class Patient(models.Model):    
    patient_id = models.CharField(max_length=14, primary_key=True)
    first_name = models.CharField(max_length=50)
    middle_name = models.CharField(max_length=50, null=True, blank=True)
    last_name = models.CharField(max_length=50)
    sex = models.CharField(max_length=6, choices=[('Male', 'Male'), ('Female', 'Female')])
    contact = models.CharField(max_length=11, default='N/A')
    street = models.CharField(max_length=100)
    barangay = models.CharField(max_length=3, default='000')
    username = models.CharField(max_length=20, unique=True)
    birthdate = models.DateField(null=True, blank=True)
    patient_pin = models.CharField(max_length=255) 
    biometric_id = models.CharField(max_length=5, unique=True, null=True, blank=True)
    date_created = models.DateTimeField(default=timezone.now)
    last_visit = models.DateTimeField(null=True, blank=True)

    def set_pin(self, raw_pin):
        """Hashes and sets the patient PIN."""
        self.patient_pin = make_password(raw_pin)  # Fixed: use patient_pin

    def check_pin(self, raw_pin):
        """Verifies a raw PIN against the stored hash."""
        return check_password(raw_pin, self.patient_pin)  # Fixed: use patient_pin
    
    @property
    def age(self):
        """Calculates age based on birthdate and today's date."""
        if self.birthdate:
            today = date.today()
            return today.year - self.birthdate.year - ((today.month, today.day) < (self.birthdate.month, self.birthdate.day))
        return None

    def save(self, *args, **kwargs):
        if not self.patient_id:
            today = timezone.now().date()
            yyyymmdd = today.strftime("%Y%m%d")

            # Keep trying until unique ID found
            for i in range(1, 1000):
                candidate_id = f"P-{yyyymmdd}-{i:03d}"
                if not Patient.objects.filter(patient_id=candidate_id).exists():
                    self.patient_id = candidate_id
                    break
            
        # Hash the PIN if it's not already hashed
        if self.patient_pin and not self.patient_pin.startswith('pbkdf2_'): 
            self.patient_pin = make_password(self.patient_pin)  
            
        # Set last_visit on first creation
        if not self.last_visit:
            self.last_visit = timezone.now()

        super().save(*args, **kwargs)

    def is_senior(self):
        """Helper: Check if patient is senior (age >= 65)."""
        return self.age is not None and self.age >= 65


class VitalSigns(models.Model):
    vitals_id = models.AutoField(primary_key=True)
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name='vital_signs', db_column='patient_id')
    pulse_rate = models.IntegerField(null=True, blank=True)  
    temperature = models.FloatField(null=True, blank=True)  
    oxygen_saturation = models.FloatField(null=True, blank=True)  
    blood_pressure = models.CharField(null=True, blank=True, max_length=7)  
    height = models.FloatField(null=True, blank=True) 
    weight = models.FloatField(null=True, blank=True) 
    date_time_recorded = models.DateTimeField(auto_now_add=True)
    
    @property
    def bmi(self):
        """Calculate BMI from height and weight"""
        if self.height and self.weight and self.height > 0:
            height_m = self.height / 100
            return round(self.weight / (height_m ** 2), 1)
        return None


class QueueEntry(models.Model):
    STATUS_CHOICES = [
        ('WAITING', 'Waiting'),
        ('COMPLETED', 'Completed'),
    ]
    
    PRIORITY_CHOICES = [
        ('CRITICAL', 'Critical'),
        ('HIGH', 'High'),
        ('NORMAL', 'Normal'),
    ]
    queue_id = models.AutoField(primary_key=True)
    patient = models.ForeignKey('Patient', on_delete=models.CASCADE, related_name='queue_entries', db_column='patient_id')
    priority_status = models.CharField(max_length=8, choices=PRIORITY_CHOICES, null=True, blank=True)
    entered_at = models.DateTimeField(default=timezone.now)
    queue_number = models.CharField(max_length=3, null=True, blank=True)  
    status = models.CharField(max_length=9, choices=STATUS_CHOICES, default='WAITING')
    served_at = models.DateTimeField(null=True, blank=True) 
    
    class Meta:
        ordering = ['-entered_at']
    
    def save(self, *args, **kwargs):
        # UPDATE LAST VISIT when entering queue
        self.patient.last_visit = timezone.now()
        self.patient.save()
        
        # Auto-compute priority on save (if not set)
        if not self.priority_status:
            from .utils import compute_patient_priority
            self.priority_status = compute_patient_priority(self.patient)
        
        # Assign queue number based on priority
        if not self.queue_number:
            today = timezone.now().date()
            
            # Determine if this is a priority patient
            is_priority = self.priority_status in ['CRITICAL', 'HIGH']  # Fixed: removed MEDIUM
            
            if is_priority:
                # Priority patients: 300-999
                highest_priority = QueueEntry.objects.filter(
                    entered_at__date=today,
                    queue_number__gte='300',
                    queue_number__lte='999'
                ).order_by('-queue_number').first()
                
                if highest_priority and highest_priority.queue_number:
                    try:
                        next_num = int(highest_priority.queue_number) + 1
                        if next_num > 999:
                            next_num = 300
                    except ValueError:
                        next_num = 300
                else:
                    next_num = 300
                
                self.queue_number = str(next_num)
            else:
                # Normal patients: 001-299
                highest_normal = QueueEntry.objects.filter(
                    entered_at__date=today,
                    queue_number__gte='001',
                    queue_number__lte='299'
                ).order_by('-queue_number').first()
                
                if highest_normal and highest_normal.queue_number:
                    try:
                        next_num = int(highest_normal.queue_number) + 1
                        if next_num > 299:
                            next_num = 1
                    except ValueError:
                        next_num = 1
                else:
                    next_num = 1
                
                self.queue_number = f"{next_num:03d}"
        
        super().save(*args, **kwargs)
    
    def mark_completed(self):
        """Mark this queue entry as completed"""
        self.status = 'COMPLETED'
        self.served_at = timezone.now()
        self.save()
    
    def mark_serving(self):
        """Mark this queue entry as currently being served"""
        self.status = 'SERVING'
        self.save()


class ArchivedPatient(models.Model):
    patient_id = models.CharField(max_length=15, primary_key=True)
    first_name = models.CharField(max_length=50)
    middle_name = models.CharField(max_length=50, null=True, blank=True)
    last_name = models.CharField(max_length=50)
    sex = models.CharField(max_length=6)
    contact = models.CharField(max_length=11)
    street = models.CharField(max_length=100, null=True, blank=True)  
    barangay = models.CharField(max_length=3, null=True, blank=True)
    username = models.CharField(max_length=20, null=True, blank=True)
    birthdate = models.DateField(null=True, blank=True)
    patient_pin = models.CharField(max_length=255)  
    biometric_id = models.CharField(max_length=5, null=True, blank=True)
    last_visit = models.DateTimeField(null=True, blank=True)
    
    # Archive metadata
    archived_at = models.DateTimeField(auto_now_add=True)
    archived_by = models.ForeignKey(HCStaff, on_delete=models.SET_NULL, null=True, blank=True)  # ADD THIS
    archive_reason = models.TextField(null=True, blank=True)  # ADD THIS
    original_created_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        db_table = 'archived_patient'
        verbose_name = 'Archived Patient'


class ArchivedVitalSigns(models.Model):
    patient = models.ForeignKey(ArchivedPatient, on_delete=models.CASCADE, related_name='vital_signs', db_column='patient_id')
    pulse_rate = models.IntegerField(null=True, blank=True)  # Fixed: match VitalSigns
    temperature = models.FloatField(null=True, blank=True)
    oxygen_saturation = models.FloatField(null=True, blank=True)
    blood_pressure = models.CharField(max_length=7, null=True, blank=True)  # Fixed: match VitalSigns
    height = models.FloatField(null=True, blank=True)
    weight = models.FloatField(null=True, blank=True)
    date_time_recorded = models.DateTimeField()
    
    # Archive metadata
    archived_at = models.DateTimeField(auto_now_add=True)
    
    @property
    def bmi(self):
        """Calculate BMI from height and weight"""
        if self.height and self.weight and self.height > 0:
            height_m = self.height / 100
            return round(self.weight / (height_m ** 2), 1)
        return None
    
    class Meta:
        db_table = 'archived_vital_signs'


class ArchivedQueueEntry(models.Model):
    patient = models.ForeignKey(ArchivedPatient, on_delete=models.CASCADE, related_name='queue_entries', db_column='patient_id')
    priority_status = models.CharField(max_length=10)
    entered_at = models.DateTimeField()
    queue_number = models.CharField(max_length=10)
    
    # Archive metadata
    archived_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'archived_queue_entry'


@transaction.atomic
def archive_patient(patient_id, staff=None, reason=None):
    """
    Archive a patient and all their related records.
    This moves data from active tables to archive tables.
    
    Args:
        patient_id: The ID of the patient to archive
        staff: HCStaff instance (optional)
        reason: Text reason for archiving (optional)
    """
    try:
        # Check if patient is already archived
        if ArchivedPatient.objects.filter(patient_id=patient_id).exists():
            return False, f"Patient {patient_id} is already archived"
        
        # Get the patient
        patient = Patient.objects.get(patient_id=patient_id)
        
        # Create archived patient record
        archived_patient = ArchivedPatient.objects.create(
            patient_id=patient.patient_id,
            first_name=patient.first_name,
            middle_name=patient.middle_name,
            last_name=patient.last_name,
            sex=patient.sex,
            contact=patient.contact,
            street=patient.street,
            barangay=patient.barangay,
            username=patient.username,
            birthdate=patient.birthdate,
            patient_pin=patient.patient_pin,
            biometric_id=patient.biometric_id,
            last_visit=patient.last_visit,
            original_created_at=patient.date_created,
            archived_by=staff,  # Pass the staff instance
            archive_reason=reason  # Pass the reason text
        )
        
        # Archive all vital signs
        vitals = VitalSigns.objects.filter(patient=patient)
        for vital in vitals:
            ArchivedVitalSigns.objects.create(
                patient=archived_patient,
                pulse_rate=vital.pulse_rate,
                temperature=vital.temperature,
                oxygen_saturation=vital.oxygen_saturation,
                blood_pressure=vital.blood_pressure,
                height=vital.height,
                weight=vital.weight,
                date_time_recorded=vital.date_time_recorded,
            )
        
        # Archive queue entries
        queue_entries = QueueEntry.objects.filter(patient=patient)
        for entry in queue_entries:
            ArchivedQueueEntry.objects.create(
                patient=archived_patient,
                priority_status=entry.priority_status,
                entered_at=entry.entered_at,
                queue_number=entry.queue_number,
            )
        
        # Delete from active tables (CASCADE will delete related records)
        patient.delete()
        
        return True, f"Patient {patient_id} archived successfully"
        
    except Patient.DoesNotExist:
        return False, f"Patient {patient_id} not found"
    except Exception as e:
        return False, f"Error archiving patient: {str(e)}"

@transaction.atomic
def restore_patient(patient_id):
    """
    Restore an archived patient back to active tables.
    """
    try:
        # Get archived patient
        archived_patient = ArchivedPatient.objects.get(patient_id=patient_id)
        
        # Check if patient already exists in active table
        if Patient.objects.filter(patient_id=patient_id).exists():
            return False, f"Patient {patient_id} already exists in active records"
        
        # Restore to Patient table
        patient = Patient.objects.create(
            patient_id=archived_patient.patient_id,
            first_name=archived_patient.first_name,
            middle_name=archived_patient.middle_name,
            last_name=archived_patient.last_name,
            sex=archived_patient.sex,
            contact=archived_patient.contact,
            street=archived_patient.street,  # Fixed
            barangay=archived_patient.barangay,  # Fixed
            username=archived_patient.username,
            birthdate=archived_patient.birthdate,
            patient_pin=archived_patient.patient_pin,  # Fixed
            biometric_id=archived_patient.biometric_id,  # Fixed
            last_visit=archived_patient.last_visit,
            date_created=archived_patient.original_created_at,  # Fixed
        )
        
        # Restore vital signs
        archived_vitals = ArchivedVitalSigns.objects.filter(patient=archived_patient)
        for vital in archived_vitals:
            VitalSigns.objects.create(
                patient=patient,
                pulse_rate=vital.pulse_rate,  # Fixed
                temperature=vital.temperature,
                oxygen_saturation=vital.oxygen_saturation,
                blood_pressure=vital.blood_pressure,
                height=vital.height,
                weight=vital.weight,
            )
        
        # Delete from archive
        archived_patient.delete()
        
        return True, f"Patient {patient_id} restored successfully"
        
    except ArchivedPatient.DoesNotExist:
        return False, f"Archived patient {patient_id} not found"
    except Exception as e:
        return False, f"Error restoring patient: {str(e)}"