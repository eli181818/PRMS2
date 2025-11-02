from django.shortcuts import render  # Unused but kept if needed elsewhere
from rest_framework import viewsets, status
from rest_framework.permissions import AllowAny
from rest_framework.decorators import action, api_view
from rest_framework.response import Response
from .models import Patient, VitalSigns, HCStaff, QueueEntry, ArchivedPatient, ArchivedVitalSigns, ArchivedQueueEntry
from .models import archive_patient, restore_patient
from .serializers import PatientSerializer, VitalSignsSerializer, QueueEntrySerializer 
from django.db.models import Q, Case, When, IntegerField, Max  # For queue sorting
from django.utils import timezone  
from .utils import compute_patient_priority
from django.views.decorators.csrf import csrf_exempt
from rest_framework.decorators import api_view


# Create your views here.

class PatientViewSet(viewsets.ModelViewSet):
    queryset = Patient.objects.all()
    serializer_class = PatientSerializer
    permission_classes = [AllowAny] 
    
    @action(detail=False, methods=['get'])  # Custom action to get patient by PIN
    def by_pin(self, request):  # GET /patients/by_pin/?pin=1234
        pin = request.query_params.get('pin')   
        if not pin:
            return Response({"error": "PIN is required"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            patient = Patient.objects.get(pin=pin)  # Fetch patient by PIN
            serializer = self.get_serializer(patient)  # Serialize the patient data
            return Response(serializer.data)  # Return serialized data
        except Patient.DoesNotExist:
            return Response({"error": "Patient not found"}, status=status.HTTP_404_NOT_FOUND)
        
    def get_queryset(self): 
        queryset = Patient.objects.all()

        # General search filter
        if self.request.query_params.get('search'):
            search_term = self.request.query_params.get('search')
            queryset = queryset.filter(
                Q(first_name__icontains=search_term) | 
                Q(last_name__icontains=search_term) | 
                Q(address__icontains=search_term) | 
                Q(patient_id__icontains=search_term) 
            )
        return queryset
         
class VitalSignsViewSet(viewsets.ModelViewSet):
    queryset = VitalSigns.objects.all()
    serializer_class = VitalSignsSerializer
    permission_classes = [AllowAny]
    
    def get_queryset(self):  # Filtering vital signs by patient_id and date range
        queryset = VitalSigns.objects.all()
        
        # Filter by patient_id
        patient_id = self.request.query_params.get('patient_id')
        if patient_id:
            queryset = queryset.filter(patient__patient_id=patient_id)
        
        # Filter by date range (fixed: use date_time_recorded)
        date_from = self.request.query_params.get('date_from')
        date_to = self.request.query_params.get('date_to')
        
        if date_from:
            queryset = queryset.filter(date_time_recorded__gte=date_from)
        if date_to:
            queryset = queryset.filter(date_time_recorded__lte=date_to)
            
        return queryset.select_related('patient').order_by('-date_time_recorded')  # Fixed: correct field
    
    @action(detail=False, methods=['get'])  # Simplified: Use query params
    def by_patient(self, request):
        patient_id = request.query_params.get('patient_id')  # GET /vitals/by_patient/?patient_id=ABC
        if not patient_id:
            return Response({"error": "patient_id is required"}, status=status.HTTP_400_BAD_REQUEST)
        vitals = VitalSigns.objects.filter(patient__patient_id=patient_id)
        serializer = self.get_serializer(vitals, many=True)
        return Response(serializer.data)

@api_view(['PUT'])
def update_vitals(request, id):
    """
    Update the vitals for a given patient_id.
    """
    try:
        patient = Patient.objects.get(patient_id=id)
        vitals_instance = VitalSigns.objects.get(patient=patient)
    except VitalSigns.DoesNotExist:
        return Response({"error": "Patient not found"}, status=status.HTTP_404_NOT_FOUND)

    serializer = VitalSignsSerializer(vitals_instance, data=request.data, partial=True)  # partial=True allows updating some fields
    # serializer = VitalSignsSerializer(vitals_instance)
    if serializer.is_valid():
        serializer.save()
        return Response({"message": "Vitals updated successfully", "data": serializer.data}, status=status.HTTP_200_OK)
    else:
        return Response({"errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
    

@api_view(['POST'])
def receive_vital_signs(request):
    """
    FOR RPi - Receives vital signs data from Raspberry Pi
    Collects partial vitals and creates a new row only when all vitals are complete.
    Allows multiple complete readings per day.
    """
    
    try:
        import pytz
        philippine_tz = pytz.timezone('Asia/Manila')
        
        data = request.data
        
        patient_id = data.get('patient_id')
        if not patient_id:
            return Response(
                {"error": "patient_id is required"}, 
                status=status.HTTP_400_BAD_REQUEST
            )        
        try:
            patient = Patient.objects.get(patient_id=patient_id)
        except Patient.DoesNotExist:
            return Response(
                {
                    "error": "Patient not found",
                    "patient_id": patient_id
                }, 
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Check if this is a "complete" signal or if all fields are present
        is_complete = data.get('complete', False)  # Frontend can send complete=true
        
        # Check which fields are present
        required_fields = ['heart_rate', 'temperature', 'oxygen_saturation', 'weight', 'height']
        all_fields_present = all(data.get(field) is not None for field in required_fields)
        
        # Get or create today's incomplete vital signs record
        today = timezone.now().date()
        today_start = timezone.datetime.combine(today, timezone.datetime.min.time())
        today_end = timezone.datetime.combine(today, timezone.datetime.max.time())
        
        if timezone.is_naive(today_start):
            today_start = timezone.make_aware(today_start)
        if timezone.is_naive(today_end):
            today_end = timezone.make_aware(today_end)
        
        # Look for an incomplete record today (one that's being built up)
        incomplete_vital = VitalSigns.objects.filter(
            patient=patient,
            date_time_recorded__range=(today_start, today_end)
        ).order_by('-date_time_recorded').first()
        
        # Check if the last record is complete
        if incomplete_vital:
            last_complete = all([
                incomplete_vital.heart_rate,
                incomplete_vital.temperature,
                incomplete_vital.oxygen_saturation,
                incomplete_vital.weight,
                incomplete_vital.height
            ])
            if last_complete:
                incomplete_vital = None  # Don't use it, it's already complete
        
        if (is_complete or all_fields_present) and incomplete_vital:
            # All vitals collected - create NEW complete record with all the data
            vital_signs = VitalSigns.objects.create(
                patient=patient,
                device_id=incomplete_vital.device_id or data.get('device_id'),
                heart_rate=incomplete_vital.heart_rate or data.get('heart_rate'),
                temperature=incomplete_vital.temperature or data.get('temperature'),
                oxygen_saturation=incomplete_vital.oxygen_saturation or data.get('oxygen_saturation'),
                weight=incomplete_vital.weight or data.get('weight'),
                height=incomplete_vital.height or data.get('height'),
            )
            
            # Delete the incomplete record
            incomplete_vital.delete()
            
            created = True
            
            # UPDATE LAST VISIT (Philippine time)
            patient.last_visit = timezone.now().astimezone(philippine_tz)
            patient.save()
            
            queue_entry, created_queue = QueueEntry.objects.get_or_create(patient=patient)
            queue_entry.priority = compute_patient_priority(patient)
            queue_entry.save()
            
            serializer = VitalSignsSerializer(vital_signs)
            patient_name = f"{patient.first_name} {patient.last_name}".strip()
            
            return Response({
                "success": True,
                "message": "Complete vital signs recorded successfully",
                "patient_name": patient_name,
                "data": serializer.data,
                "all_complete": True
            }, status=status.HTTP_201_CREATED)
        
        else:
            # Partial data - update or create incomplete record
            if incomplete_vital:
                # Update existing incomplete record
                if data.get('device_id') is not None:
                    incomplete_vital.device_id = data.get('device_id')
                if data.get('heart_rate') is not None:
                    incomplete_vital.heart_rate = data.get('heart_rate')
                if data.get('temperature') is not None:
                    incomplete_vital.temperature = data.get('temperature')
                if data.get('oxygen_saturation') is not None:
                    incomplete_vital.oxygen_saturation = data.get('oxygen_saturation')
                if data.get('weight') is not None:
                    incomplete_vital.weight = data.get('weight')
                if data.get('height') is not None:
                    incomplete_vital.height = data.get('height')
                
                incomplete_vital.save()
                vital_signs = incomplete_vital
                created = False
            else:
                # Create new incomplete record
                vital_signs = VitalSigns.objects.create(
                    patient=patient,
                    device_id=data.get('device_id'),
                    heart_rate=data.get('heart_rate'),
                    temperature=data.get('temperature'),
                    oxygen_saturation=data.get('oxygen_saturation'),
                    weight=data.get('weight'),
                    height=data.get('height'),
                )
                created = True
            
            # Check what's still missing
            missing_fields = []
            if not vital_signs.heart_rate:
                missing_fields.append('heart_rate')
            if not vital_signs.temperature:
                missing_fields.append('temperature')
            if not vital_signs.oxygen_saturation:
                missing_fields.append('oxygen_saturation')
            if not vital_signs.weight:
                missing_fields.append('weight')
            if not vital_signs.height:
                missing_fields.append('height')
            
            serializer = VitalSignsSerializer(vital_signs)
            
            return Response({
                "success": True,
                "message": "Partial vital signs saved",
                "data": serializer.data,
                "missing_fields": missing_fields,
                "all_complete": False
            }, status=status.HTTP_200_OK)
        
    except Exception as e:
        print(f"Error receiving vitals: {str(e)}")  # For debugging
        import traceback
        traceback.print_exc()  # Print full stack trace
        return Response({
            "error": "Server error",
            "details": str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['GET'])
def test_rpi_connection(request):
    """
    Simple test endpoint to verify RPi can connect to Django
    """
    return Response({
        "status": "connected",
        "message": "Django server is reachable from Raspberry Pi",
        "timestamp": timezone.now().isoformat()
    })

@csrf_exempt
@api_view(['POST'])
def login(request):
    pin = request.data.get("pin")
    login_type = request.data.get("login_type")  # 'staff' or 'patient'
    username = request.data.get("username")  # For patient login
    
    if not pin:
        return Response({"error": "PIN required"}, status=status.HTTP_400_BAD_REQUEST)
    
    pin = str(pin).strip()
    
    # Staff login
    if login_type == 'staff':
        try:
            staff = HCStaff.objects.get(staff_pin=pin)
            
            # CREATE SESSION (server-side)
            request.session['user_id'] = staff.id
            request.session['user_type'] = 'staff'
            request.session['name'] = staff.name
            
            return Response({
                "role": "staff",
                "name": staff.name
            })
            
        except HCStaff.DoesNotExist:
            return Response({"error": "Invalid staff PIN"}, status=status.HTTP_401_UNAUTHORIZED)
    
    # Patient login (requires both username and PIN)
    elif login_type == 'patient':
        if not username:
            return Response({"error": "Username required for patient login"}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            patient = Patient.objects.get(username=username.strip(), pin=pin)
            
            request.session['user_type'] = 'patient'
            request.session['patient_id'] = patient.patient_id
            
            return Response({
                "role": "patient",
                "patient_id": patient.patient_id,  # Just the ID, not full data
                "name": f"{patient.first_name} {patient.last_name}"
            })
            
        except Patient.DoesNotExist:
            return Response({"error": "Invalid username or PIN"}, status=status.HTTP_401_UNAUTHORIZED)
    
    return Response({"error": "Invalid login type"}, status=status.HTTP_400_BAD_REQUEST)

@api_view(['GET'])
def get_patient_profile(request):
    """Get current logged-in patient's profile"""
    user_type = request.session.get('user_type')
    
    if user_type != 'patient':
        return Response({"error": "Not authenticated as patient"}, status=status.HTTP_401_UNAUTHORIZED)
    
    patient_id = request.session.get('patient_id')
    
    try:
        patient = Patient.objects.get(patient_id=patient_id)
        serializer = PatientSerializer(patient)
        return Response(serializer.data)
    except Patient.DoesNotExist:
        return Response({"error": "Patient not found"}, status=status.HTTP_404_NOT_FOUND)
    
@api_view(['GET'])
def get_patient_vitals(request):
    """Get current logged-in patient's vitals history"""
    user_type = request.session.get('user_type')
    
    if user_type != 'patient':
        return Response({"error": "Not authenticated as patient"}, status=status.HTTP_401_UNAUTHORIZED)
    
    patient_id = request.session.get('patient_id')
    
    try:
        patient = Patient.objects.get(patient_id=patient_id)
        
        # Get all vitals for this patient, ordered by most recent first
        vitals_queryset = VitalSigns.objects.filter(patient=patient).order_by('-date_time_recorded')
        
        # Get latest vitals (most recent)
        latest_vital = vitals_queryset.first()
        
        latest_data = None
        if latest_vital:
            # Calculate BMI if height and weight exist
            bmi_value = None
            if latest_vital.height and latest_vital.weight:
                height_m = latest_vital.height / 100  # Convert cm to meters
                bmi_value = round(latest_vital.weight / (height_m * height_m), 1)
            
            latest_data = {
                'heart_rate': latest_vital.heart_rate,
                'temperature': latest_vital.temperature,
                'spo2': latest_vital.oxygen_saturation,
                'blood_pressure': None,  # Add blood pressure fields to your model if needed
                'height': latest_vital.height,
                'weight': latest_vital.weight,
                'bmi': bmi_value
            }
        
        # Get history (all records)
        history_data = []
        for vital in vitals_queryset:
            history_data.append({
                'id': vital.id,
                'date': vital.date_time_recorded.strftime('%Y-%m-%d %H:%M'),
                'heart_rate': vital.heart_rate,
                'blood_pressure': None,  # Add blood pressure fields to your model if needed
                'temperature': vital.temperature,
                'spo2': vital.oxygen_saturation,
                'height': vital.height,
                'weight': vital.weight,
                'bmi': vital.bmi
            })
        
        return Response({
            'latest': latest_data,
            'history': history_data
        })
        
    except Patient.DoesNotExist:
        return Response({"error": "Patient not found"}, status=status.HTTP_404_NOT_FOUND)
    
@api_view(['GET'])
def get_vitals(request):
   # Add auth check if needed (e.g., permission_classes = [IsAuthenticated])
    patients = VitalSigns.objects.all()
    serializer = VitalSignsSerializer(patients, many=True)
    return Response(serializer.data)

@api_view(['GET'])
def get_patient_vitals_by_id(request, patient_id): # <-- NEW FUNCTION
    """Get a patient's vitals history using patient_id (for staff view)"""
    try:
        # 1. Use the provided patient_id to find the Patient object
        patient = Patient.objects.get(patient_id=patient_id)
        
        # 2. Get all vitals for this patient, ordered by most recent first
        vitals_queryset = VitalSigns.objects.filter(patient=patient).order_by('-date_time_recorded')
        
        latest_vital = vitals_queryset.first()
        latest_data = None
        
        if latest_vital:
            # Calculate BMI if height and weight exist
            bmi_value = None
            if latest_vital.height and latest_vital.weight:
                height_m = latest_vital.height / 100
                bmi_value = round(latest_vital.weight / (height_m * height_m), 1)
            
            # Map latest vitals data
            latest_data = {
                'heart_rate': latest_vital.heart_rate,
                'temperature': latest_vital.temperature,
                'oxygen_saturation': latest_vital.oxygen_saturation,
                'blood_pressure': latest_vital.blood_pressure, # ADDED: Ensure BP is included
                'height': latest_vital.height,
                'weight': latest_vital.weight,
                'bmi': bmi_value
            }
        
        # 3. Get history (all records)
        history_data = []
        for vital in vitals_queryset:
            bmi_value = None
            if vital.height and vital.weight:
                height_m = vital.height / 100
                bmi_value = round(vital.weight / (height_m * height_m), 1)

            history_data.append({
                'id': vital.id,
                'date': vital.date_time_recorded.strftime('%Y-%m-%d %H:%M'), 
                'heart_rate': vital.heart_rate,
                'blood_pressure': vital.blood_pressure,
                'temperature': vital.temperature,
                'oxygen_saturation': vital.oxygen_saturation,
                'height': vital.height,
                'weight': vital.weight,
                'bmi': bmi_value 
            })
        
        return Response({
            'latest': latest_data,
            'history': history_data
        })
        
    except Patient.DoesNotExist:
        return Response({"error": "Patient not found"}, status=status.HTTP_404_NOT_FOUND)

class QueueViewSet(viewsets.ModelViewSet):
    queryset = QueueEntry.objects.all()
    serializer_class = QueueEntrySerializer
    permission_classes = [AllowAny]  # Restrict in production
    
    @action(detail=False, methods=['get'])
    def current_queue(self, request):
        """Get sorted queue: Prioritize by priority level, then entered_at (earliest first)."""
        queue = QueueEntry.objects.all().select_related(
            'patient', 'patient__vital_signs'  # Fixed: correct related_name
        ).annotate(
            priority_order=Case(
                When(priority='CRITICAL', then=1),
                When(priority='HIGH', then=2),
                When(priority='MEDIUM', then=3),
                default=4,
                output_field=IntegerField()
            )
        ).order_by('priority_order', 'entered_at')
        
        serializer = self.get_serializer(queue, many=True)
        return Response(serializer.data)
 

@api_view(['POST'])
def logout(request):
    """Clear session"""
    request.session.flush()
    return Response({"message": "Logged out successfully"})

@api_view(['GET'])
def get_all_patients(request):
    """
    Retrieves all patients and attaches the latest vital signs
    to each patient object under the 'latest_vitals' key.
    """
    patients_queryset = Patient.objects.all().order_by('id')
    
    # 1. Efficiently find the ID of the LATEST VitalSigns record for each patient
    latest_vitals_map = VitalSigns.objects.filter(
        patient__in=patients_queryset
    ).values('patient').annotate(
        latest_id=Max('id')
    ).values_list('latest_id', flat=True)

    # 2. Fetch the actual latest VitalSigns objects using their IDs
    latest_vitals = VitalSigns.objects.filter(id__in=latest_vitals_map)
    # Map them by patient_id (database ID) for easy lookup
    vitals_dict = {v.patient_id: v for v in latest_vitals}

    # 3. Serialize patients
    serializer = PatientSerializer(patients_queryset, many=True)
    
    data = serializer.data
    
    # 4. Inject latest_vitals data into the serialized output
    for patient_data in data:
        patient_db_id = patient_data['id'] 
        vital = vitals_dict.get(patient_db_id)
        
        latest_vital_data = None
        if vital:
            # Calculate BMI
            bmi_value = None
            if vital.height and vital.weight:
                height_m = vital.height / 100
                bmi_value = round(vital.weight / (height_m * height_m), 1)

            latest_vital_data = {
                'heart_rate': vital.heart_rate,
                'temperature': vital.temperature,
                'oxygen_saturation': vital.oxygen_saturation,
                'blood_pressure': vital.blood_pressure,
                'height': vital.height,
                'weight': vital.weight,
                'bmi': bmi_value, 
            }
        
        # This key 'latest_vitals' is what the frontend expects
        patient_data['latest_vitals'] = latest_vital_data 

    return Response(data)

class QueueViewSet(viewsets.ModelViewSet):
    queryset = QueueEntry.objects.all()
    serializer_class = QueueEntrySerializer
    permission_classes = [AllowAny]  # Restrict in production
    
    @action(detail=False, methods=['get'])
    def current_queue(self, request):
        """Get sorted queue: Prioritize by priority level, then entered_at (earliest first)."""
        queue = QueueEntry.objects.all().select_related(
            'patient', 'patient__vital_signs'  # Fixed: correct related_name
        ).annotate(
            priority_order=Case(
                When(priority='CRITICAL', then=1),
                When(priority='HIGH', then=2),
                When(priority='MEDIUM', then=3),
                default=4,
                output_field=IntegerField()
            )
        ).order_by('priority_order', 'entered_at')
        
        serializer = self.get_serializer(queue, many=True)
        return Response(serializer.data)

@api_view(['POST'])
def archive_patient_view(request, patient_id):
    """Archive a patient and all their records"""
    staff = None  # Get from session if needed
    reason = request.data.get('reason', 'No reason provided')
    
    success, message = archive_patient(patient_id, staff, reason)
    
    if success:
        return Response({"message": message}, status=status.HTTP_200_OK)
    else:
        return Response({"error": message}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
def restore_patient_view(request, patient_id):
    """Restore an archived patient"""
    success, message = restore_patient(patient_id)
    
    if success:
        return Response({"message": message}, status=status.HTTP_200_OK)
    else:
        return Response({"error": message}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
def get_archived_patients(request):
    """Get list of archived patients"""
    archived = ArchivedPatient.objects.all().order_by('-archived_at')
    
    data = [{
        'patient_id': p.patient_id,
        'name': f"{p.first_name} {p.last_name}",
        'archived_at': p.archived_at,
        'archived_by': p.archived_by.name if p.archived_by else None,
        'archive_reason': p.archive_reason,
    } for p in archived]
    
    return Response(data)

 