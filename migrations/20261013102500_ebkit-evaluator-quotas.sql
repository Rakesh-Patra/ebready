CREATE OR REPLACE FUNCTION public.ebkit_reserve_trial(p_project jsonb, p_capacity integer) RETURNS jsonb
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE v_project public.ebkit_web_projects; v_count integer;
BEGIN
  IF p_capacity < 1 OR p_capacity > 10 THEN RAISE EXCEPTION 'Invalid global capacity'; END IF;
  PERFORM pg_advisory_xact_lock(784231902);
  IF NOT coalesce((p_project->>'operator_evaluator')::boolean,false) THEN
  IF coalesce((p_project->>'operator_test')::boolean,false) THEN
    SELECT count(*) INTO v_count FROM public.ebkit_web_projects WHERE user_id=(p_project->>'user_id')::uuid AND mode='live' AND (status<>'destroyed' OR (plan='free_trial' AND trial_cleanup_state IS DISTINCT FROM 'complete'));
    IF v_count >= 10 THEN RAISE EXCEPTION 'TEST_CAPACITY_FULL'; END IF;
  ELSIF EXISTS(SELECT 1 FROM public.ebkit_web_trial_claims WHERE user_id=(p_project->>'user_id')::uuid) THEN RAISE EXCEPTION 'TRIAL_USED'; END IF;
  SELECT count(*) INTO v_count FROM public.ebkit_web_projects WHERE mode='live' AND (status<>'destroyed' OR (plan='free_trial' AND trial_cleanup_state IS DISTINCT FROM 'complete'));
  IF v_count >= (CASE WHEN coalesce((p_project->>'operator_test')::boolean,false) THEN 10 ELSE p_capacity END) THEN RAISE EXCEPTION 'CAPACITY_FULL'; END IF;
  END IF;
  INSERT INTO public.ebkit_web_projects(id,user_id,name,repository,region,port,mode,status,stage,replicas,revision,variable_names,application_name,environment_name,plan,trial_reserved_at,trial_expires_at,trial_cleanup_state,trial_cleanup_next_at)
  VALUES((p_project->>'id')::uuid,(p_project->>'user_id')::uuid,p_project->>'name',p_project->>'repository',p_project->>'region',(p_project->>'port')::integer,'live','deploying',1,1,1,ARRAY(SELECT jsonb_array_elements_text(p_project->'variable_names')),p_project->>'application_name',p_project->>'environment_name','free_trial',now(),now()+interval '30 minutes','scheduled',now()+interval '30 minutes') RETURNING * INTO v_project;
  IF coalesce((p_project->>'operator_evaluator')::boolean,false) THEN
    UPDATE public.ebkit_web_projects SET plan=NULL,trial_reserved_at=NULL,trial_started_at=NULL,trial_expires_at=NULL,trial_cleanup_state=NULL,trial_cleanup_next_at=NULL WHERE id=v_project.id RETURNING * INTO v_project;
  END IF;
  INSERT INTO public.ebkit_web_trial_claims(user_id,project_id) VALUES(v_project.user_id,v_project.id) ON CONFLICT(user_id) DO NOTHING;
  RETURN to_jsonb(v_project);
END $$;
REVOKE ALL ON FUNCTION public.ebkit_reserve_trial(jsonb,integer) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.ebkit_reserve_trial(jsonb,integer) TO project_admin;

