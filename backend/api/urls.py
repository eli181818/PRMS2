from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (PatientViewSet, VitalSignsViewSet, QueueViewSet, login, 
                    get_vitals, receive_vital_signs, update_vitals, get_all_patients, 
                    test_rpi_connection, logout, get_patient_profile, get_patient_vitals,
                    get_patient_vitals_by_id, archive_patient_view, restore_patient_view,
                    get_archived_patients, store_biometric, verify_biometric, 
                    start_biometric_enrollment,
                    check_enrollment_status, delete_biometric, get_biometric_count,
                    start_biometric_scan, check_biometric_match, stop_biometric_scan
                )
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register(r'patients', PatientViewSet)
router.register(r'vitals', VitalSignsViewSet)
router.register(r'queue', QueueViewSet)

urlpatterns = [ # endpoints
    path('login/', login, name="login"),
    path('logout/', logout, name="logout"),
    path('patient/profile/', get_patient_profile, name='patient_profile'),
    path('patient/vitals/', get_patient_vitals, name='patient_vitals'), 
    path('patient/vitals/<str:patient_id>/', get_patient_vitals_by_id, name='patient_vitals_by_id'),
    path('', include(router.urls)), # includes the viewsets for patients and vitals
    path('all-patients/', get_all_patients, name='all_patients'),
    path('receive-vitals/', receive_vital_signs, name='receive_vitals'),
    path('update-vitals/<str:patient_id>', update_vitals, name='update_vitals'),
    path('test-connection/', test_rpi_connection, name='test_connection'),
    path('archive-patient/<str:patient_id>/', archive_patient_view, name='archive_patient'),
    path('restore-patient/<str:patient_id>/', restore_patient_view, name='restore_patient'),
    path('archived-patients/', get_archived_patients, name='archived_patients'),
    path('store-biometric/', store_biometric, name='store_biometric'),
    path('verify-biometric/', verify_biometric, name='verify_biometric'),
    path('', include(router.urls)),
    path('login/', login, name='login'),
    path('receive-vitals/', receive_vital_signs, name='receive_vitals'),
    path('all-patients/', get_all_patients, name='all_patients'),
    path('test-connection/', test_rpi_connection, name='test_connection'),
    path('start_vitals/', views.start_vitals, name='start_vitals'),
    path('fetch_temperature/', views.fetch_temperature, name='fetch_temperature'),
    path('fetch_pulse_rate/', views.fetch_pulse_rate, name='fetch_pulse_rate'),
    path('fetch_spo2/', views.fetch_spo2, name='fetch_spo2'),
    path('fetch_height/', views.fetch_height, name='fetch_height'),
    path('biometric/enroll/', start_biometric_enrollment, name='start_biometric_enrollment'),
    path('biometric/status/', check_enrollment_status, name='check_enrollment_status'),
    path('biometric/delete/<str:patient_id>/', delete_biometric, name='delete_biometric'),
    path('biometric/count/', get_biometric_count, name='biometric_count'),
    path('biometric/scan/', start_biometric_scan, name='start_biometric_scan'),
    path('biometric/match/', check_biometric_match, name='check_biometric_match'),
    path('biometric/stop/', stop_biometric_scan, name='stop_biometric_scan'),
]



