import logging


from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView


from hrms.models import Application, RecruitmentAuditLog


logger = logging.getLogger(__name__)




class ApplicationStageMoveAPIView(APIView):
   """Move an application to a new pipeline stage via drag-and-drop."""


   permission_classes = [IsAuthenticated]


   def _status_from_stage_name(self, stage_name):
       stage_name = (stage_name or '').lower()
       if 'reject' in stage_name:
           return Application.Status.REJECTED
       if 'hire' in stage_name:
           return Application.Status.HIRED
       if 'offer' in stage_name:
           return Application.Status.OFFERED
       if 'interview' in stage_name:
           return Application.Status.INTERVIEWING
       if 'shortlist' in stage_name:
           return Application.Status.SHORTLISTED
       return Application.Status.APPLIED


   def post(self, request, pk, *args, **kwargs):
       target_stage_id = request.data.get('target_stage_id')
       if target_stage_id is None or target_stage_id == '':
           raise DRFValidationError({'target_stage_id': 'This field is required.'})


       try:
           target_stage_id = int(target_stage_id)
       except (TypeError, ValueError):
           raise DRFValidationError({'target_stage_id': 'Expected an integer value.'})


       application = get_object_or_404(Application, pk=pk)


       if application.is_locked and not request.user.is_superuser:
           raise DRFValidationError({
               'detail': 'This application is locked and cannot be modified by non-admin users.'
           })


       target_stage_record = (
           application.job_posting.pipeline_stages
           .select_related('stage')
           .filter(stage_id=target_stage_id)
           .first()
       )
       if not target_stage_record:
           raise DRFValidationError({
               'target_stage_id': 'The selected stage is not part of this job posting pipeline.'
           })


       previous_stage = application.current_stage
       previous_status = application.status
       new_status = self._status_from_stage_name(target_stage_record.stage.name)


       with transaction.atomic():
           application.current_stage = target_stage_record.stage
           application.status = new_status
           application.save(update_fields=['current_stage', 'status', 'updated_at'])


           if new_status in [Application.Status.HIRED, Application.Status.REJECTED]:
               application.is_locked = True
               application.locked_by = request.user
               application.locked_at = timezone.now()
               application.save(update_fields=['is_locked', 'locked_by', 'locked_at', 'updated_at'])


           RecruitmentAuditLog.objects.create(
               application=application,
               from_status=previous_status,
               to_status=new_status,
               action=f"Moved application from {previous_stage.name if previous_stage else 'Unassigned'} to {target_stage_record.stage.name}",
               performed_by=request.user,
               note=f"Stage drag-and-drop update performed by {request.user.get_full_name() or request.user.username}",
           )


       return Response(
           {
               'id': application.pk,
               'candidate_id': application.candidate_id,
               'job_posting_id': application.job_posting_id,
               'from_stage_id': previous_stage.pk if previous_stage else None,
               'to_stage_id': target_stage_record.stage.pk,
               'from_status': previous_status,
               'to_status': new_status,
               'is_locked': application.is_locked,
           },
           status=status.HTTP_200_OK,
       )




class BulkResumeUploadAPIView(APIView):
   """Upload individual PDFs or a ZIP archive of resumes for a job posting."""


   permission_classes = [IsAuthenticated]


   def post(self, request, job_id, *args, **kwargs):
       from django.shortcuts import get_object_or_404
       from django.core.files.uploadedfile import SimpleUploadedFile
       from hrms.models import JobPosting
       from hrms.services import create_candidate_from_resume
       import io
       import os
       import zipfile


       job_posting = get_object_or_404(JobPosting, pk=job_id)
       uploaded_files = list(request.FILES.getlist('files'))
       zip_file = request.FILES.get('zip_file')

       if not zip_file:
           for f in list(uploaded_files):
               if str(getattr(f, 'name', '')).lower().endswith('.zip'):
                   zip_file = f
                   uploaded_files.remove(f)
                   break

       if not uploaded_files and not zip_file:
           raise DRFValidationError({'detail': 'Upload at least one PDF file or a ZIP archive.'})


       created_candidates = []
       errors = []
       skipped = []


       try:
           with transaction.atomic():
               if zip_file:
                   if not str(zip_file.name).lower().endswith('.zip'):
                       raise DRFValidationError({'detail': 'The uploaded archive must be a .zip file.'})
                   try:
                       zip_bytes = zip_file.read()
                       with zipfile.ZipFile(io.BytesIO(zip_bytes), 'r') as archive:
                           for member in archive.infolist():
                               if member.is_dir() or not member.filename.lower().endswith('.pdf'):
                                   continue
                               try:
                                   file_bytes = archive.read(member.filename)
                                   uploaded = SimpleUploadedFile(
                                       name=os.path.basename(member.filename),
                                       content=file_bytes,
                                       content_type='application/pdf',
                                   )
                                   result = create_candidate_from_resume(uploaded, job_posting.pk)
                                   created_candidates.append(result)
                               except Exception as exc:
                                   errors.append({'filename': os.path.basename(member.filename), 'error': str(exc)})
                   except zipfile.BadZipFile:
                       raise DRFValidationError({'detail': 'The uploaded file is not a valid ZIP archive.'})


               for uploaded in uploaded_files:
                   filename = getattr(uploaded, 'name', 'resume.pdf')
                   if not filename.lower().endswith('.pdf'):
                       skipped.append({'filename': filename, 'reason': 'Only PDF files are accepted.'})
                       continue
                   try:
                       result = create_candidate_from_resume(uploaded, job_posting.pk)
                       created_candidates.append(result)
                   except Exception as exc:
                       errors.append({'filename': filename, 'error': str(exc)})
       except DRFValidationError:
           raise
       except Exception as exc:
           logger.exception('Bulk resume upload failed for job %s', job_id)
           return Response({'detail': 'Bulk resume upload failed.', 'error': str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


       return Response(
           {
               'job_id': job_posting.pk,
               'processed_count': len(created_candidates),
               'created_count': len(created_candidates),
               'skipped': skipped,
               'errors': errors,
               'created_candidates': created_candidates,
           },
           status=status.HTTP_201_CREATED if created_candidates else status.HTTP_200_OK,
       )
