ALTER TABLE public.ebkit_web_projects
  ADD COLUMN plan text CHECK (plan IS NULL OR plan = 'free_trial'),
  ADD COLUMN trial_reserved_at timestamptz,
  ADD COLUMN trial_started_at timestamptz,
  ADD COLUMN trial_expires_at timestamptz,
  ADD COLUMN trial_cleanup_state text CHECK (trial_cleanup_state IS NULL OR trial_cleanup_state IN ('scheduled','pending','destroying','verifying','complete')),
  ADD COLUMN trial_cleanup_next_at timestamptz,
  ADD COLUMN trial_cleanup_attempts integer NOT NULL DEFAULT 0;
CREATE INDEX ebkit_web_trials_due ON public.ebkit_web_projects(trial_cleanup_next_at) WHERE plan='free_trial' AND trial_cleanup_state <> 'complete';
-- Keep claims even if an account or project is deleted: deletion must not reset a trial.
CREATE TABLE public.ebkit_web_trial_claims(user_id uuid PRIMARY KEY, project_id uuid UNIQUE NOT NULL, claimed_at timestamptz NOT NULL DEFAULT now());
ALTER TABLE public.ebkit_web_trial_claims ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.ebkit_web_trial_claims FROM PUBLIC,anon,authenticated;
GRANT SELECT ON public.ebkit_web_trial_claims TO authenticated;
CREATE POLICY own_trial_claim ON public.ebkit_web_trial_claims FOR SELECT TO authenticated USING(user_id=auth.uid());

CREATE FUNCTION public.ebkit_reserve_trial(p_project jsonb, p_capacity integer) RETURNS jsonb
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE v_project public.ebkit_web_projects; v_count integer;
BEGIN
  IF p_capacity < 1 OR p_capacity > 10 THEN RAISE EXCEPTION 'Invalid global capacity'; END IF;
  PERFORM pg_advisory_xact_lock(784231902);
  IF EXISTS(SELECT 1 FROM public.ebkit_web_trial_claims WHERE user_id=(p_project->>'user_id')::uuid) THEN RAISE EXCEPTION 'TRIAL_USED'; END IF;
  SELECT count(*) INTO v_count FROM public.ebkit_web_projects WHERE mode='live' AND (status<>'destroyed' OR (plan='free_trial' AND trial_cleanup_state IS DISTINCT FROM 'complete'));
  IF v_count >= p_capacity THEN RAISE EXCEPTION 'CAPACITY_FULL'; END IF;
  INSERT INTO public.ebkit_web_projects(id,user_id,name,repository,region,port,mode,status,stage,replicas,revision,variable_names,application_name,environment_name,plan,trial_reserved_at,trial_expires_at,trial_cleanup_state,trial_cleanup_next_at)
  VALUES((p_project->>'id')::uuid,(p_project->>'user_id')::uuid,p_project->>'name',p_project->>'repository',p_project->>'region',(p_project->>'port')::integer,'live','deploying',1,1,1,ARRAY(SELECT jsonb_array_elements_text(p_project->'variable_names')),p_project->>'application_name',p_project->>'environment_name','free_trial',now(),now()+interval '30 minutes','scheduled',now()+interval '30 minutes') RETURNING * INTO v_project;
  INSERT INTO public.ebkit_web_trial_claims(user_id,project_id) VALUES(v_project.user_id,v_project.id);
  RETURN to_jsonb(v_project);
END $$;
REVOKE ALL ON FUNCTION public.ebkit_reserve_trial(jsonb,integer) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.ebkit_reserve_trial(jsonb,integer) TO project_admin;

CREATE FUNCTION public.ebkit_start_trial(p_id uuid) RETURNS jsonb
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE v_project public.ebkit_web_projects;
BEGIN
  UPDATE public.ebkit_web_projects SET trial_started_at=now(),trial_expires_at=now()+interval '30 minutes',trial_cleanup_next_at=now()+interval '30 minutes'
    WHERE id=p_id AND plan='free_trial' AND trial_started_at IS NULL AND trial_cleanup_state='scheduled' AND trial_expires_at>now();
  SELECT * INTO v_project FROM public.ebkit_web_projects WHERE id=p_id AND plan='free_trial';
  RETURN jsonb_build_object('trial_started_at',v_project.trial_started_at,'trial_expires_at',v_project.trial_expires_at,'trial_cleanup_next_at',v_project.trial_cleanup_next_at);
END $$;
REVOKE ALL ON FUNCTION public.ebkit_start_trial(uuid) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.ebkit_start_trial(uuid) TO project_admin;
