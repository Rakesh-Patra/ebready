ALTER TABLE public.ebkit_web_projects
  DROP CONSTRAINT IF EXISTS ebkit_web_projects_plan_check,
  ADD CONSTRAINT ebkit_web_projects_plan_check CHECK (plan IS NULL OR plan IN ('free_trial','paid_test'));

CREATE INDEX ebkit_web_paid_tests_due
  ON public.ebkit_web_projects(trial_cleanup_next_at)
  WHERE plan='paid_test' AND trial_cleanup_state <> 'complete';

CREATE OR REPLACE FUNCTION public.ebkit_reserve_trial(p_project jsonb, p_capacity integer) RETURNS jsonb
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE v_project public.ebkit_web_projects; v_count integer; v_paid_test boolean; v_evaluator boolean; v_test boolean;
BEGIN
  IF p_capacity < 1 OR p_capacity > 10 THEN RAISE EXCEPTION 'Invalid global capacity'; END IF;
  v_paid_test := coalesce((p_project->>'operator_paid_test')::boolean,false);
  v_evaluator := coalesce((p_project->>'operator_evaluator')::boolean,false);
  v_test := coalesce((p_project->>'operator_test')::boolean,false);
  PERFORM pg_advisory_xact_lock(784231902);
  IF NOT v_evaluator AND EXISTS(
    SELECT 1 FROM public.ebkit_web_projects
    WHERE user_id=(p_project->>'user_id')::uuid AND mode='live' AND plan='paid_test'
      AND (status<>'destroyed' OR trial_cleanup_state IS DISTINCT FROM 'complete')
  ) THEN RAISE EXCEPTION 'PAID_TEST_ACTIVE'; END IF;
  IF v_paid_test AND EXISTS(
    SELECT 1 FROM public.ebkit_web_projects
    WHERE user_id=(p_project->>'user_id')::uuid AND mode='live'
      AND (status<>'destroyed' OR (plan IN ('free_trial','paid_test') AND trial_cleanup_state IS DISTINCT FROM 'complete'))
  ) THEN RAISE EXCEPTION 'PAID_TEST_ACTIVE'; END IF;
  IF NOT v_evaluator THEN
    IF NOT v_paid_test THEN
      IF v_test THEN
        SELECT count(*) INTO v_count FROM public.ebkit_web_projects
        WHERE user_id=(p_project->>'user_id')::uuid AND mode='live'
          AND (status<>'destroyed' OR (plan IN ('free_trial','paid_test') AND trial_cleanup_state IS DISTINCT FROM 'complete'));
        IF v_count >= 10 THEN RAISE EXCEPTION 'TEST_CAPACITY_FULL'; END IF;
      ELSIF EXISTS(SELECT 1 FROM public.ebkit_web_trial_claims WHERE user_id=(p_project->>'user_id')::uuid) THEN
        RAISE EXCEPTION 'TRIAL_USED';
      END IF;
    END IF;
    SELECT count(*) INTO v_count FROM public.ebkit_web_projects
    WHERE mode='live' AND (status<>'destroyed' OR (plan IN ('free_trial','paid_test') AND trial_cleanup_state IS DISTINCT FROM 'complete'));
    IF v_count >= (CASE WHEN v_test AND NOT v_paid_test THEN 10 ELSE p_capacity END) THEN RAISE EXCEPTION 'CAPACITY_FULL'; END IF;
  END IF;
  INSERT INTO public.ebkit_web_projects(id,user_id,name,repository,region,port,mode,status,stage,replicas,revision,variable_names,application_name,environment_name,plan,trial_reserved_at,trial_expires_at,trial_cleanup_state,trial_cleanup_next_at)
  VALUES((p_project->>'id')::uuid,(p_project->>'user_id')::uuid,p_project->>'name',p_project->>'repository',p_project->>'region',(p_project->>'port')::integer,'live','deploying',1,1,1,ARRAY(SELECT jsonb_array_elements_text(p_project->'variable_names')),p_project->>'application_name',p_project->>'environment_name',CASE WHEN v_paid_test THEN 'paid_test' ELSE 'free_trial' END,now(),now()+interval '30 minutes','scheduled',now()+interval '30 minutes') RETURNING * INTO v_project;
  IF v_evaluator AND NOT v_paid_test THEN
    UPDATE public.ebkit_web_projects SET plan=NULL,trial_reserved_at=NULL,trial_started_at=NULL,trial_expires_at=NULL,trial_cleanup_state=NULL,trial_cleanup_next_at=NULL WHERE id=v_project.id RETURNING * INTO v_project;
  END IF;
  IF NOT v_paid_test THEN
    INSERT INTO public.ebkit_web_trial_claims(user_id,project_id) VALUES(v_project.user_id,v_project.id) ON CONFLICT(user_id) DO NOTHING;
  END IF;
  RETURN to_jsonb(v_project);
END $$;
REVOKE ALL ON FUNCTION public.ebkit_reserve_trial(jsonb,integer) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.ebkit_reserve_trial(jsonb,integer) TO project_admin;

CREATE OR REPLACE FUNCTION public.ebkit_start_trial(p_id uuid) RETURNS jsonb
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE v_project public.ebkit_web_projects;
BEGIN
  UPDATE public.ebkit_web_projects
    SET trial_started_at=now(),
        trial_expires_at=now()+CASE WHEN plan='paid_test' THEN interval '120 minutes' ELSE interval '30 minutes' END,
        trial_cleanup_next_at=now()+CASE WHEN plan='paid_test' THEN interval '120 minutes' ELSE interval '30 minutes' END
    WHERE id=p_id AND plan IN ('free_trial','paid_test') AND trial_started_at IS NULL
      AND trial_cleanup_state='scheduled' AND trial_expires_at>now();
  SELECT * INTO v_project FROM public.ebkit_web_projects WHERE id=p_id AND plan IN ('free_trial','paid_test');
  RETURN jsonb_build_object('trial_started_at',v_project.trial_started_at,'trial_expires_at',v_project.trial_expires_at,'trial_cleanup_next_at',v_project.trial_cleanup_next_at);
END $$;
REVOKE ALL ON FUNCTION public.ebkit_start_trial(uuid) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.ebkit_start_trial(uuid) TO project_admin;
