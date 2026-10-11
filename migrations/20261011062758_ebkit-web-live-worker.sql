ALTER TABLE public.ebkit_web_projects DROP CONSTRAINT ebkit_web_projects_mode_check;
ALTER TABLE public.ebkit_web_projects ADD CONSTRAINT ebkit_web_projects_mode_check CHECK (mode IN ('demo','live'));
ALTER TABLE public.ebkit_web_projects DROP CONSTRAINT ebkit_web_projects_status_check;
ALTER TABLE public.ebkit_web_projects ADD CONSTRAINT ebkit_web_projects_status_check CHECK (status IN ('deploying','ready','failed','destroying','destroyed'));
ALTER TABLE public.ebkit_web_projects
  ADD COLUMN application_name text,
  ADD COLUMN environment_name text,
  ADD COLUMN live_url text,
  ADD COLUMN health text,
  ADD COLUMN last_error text,
  ADD COLUMN operation_log text,
  ADD COLUMN last_operation text,
  ADD COLUMN operation_running boolean NOT NULL DEFAULT false;
DROP POLICY owner_insert ON public.ebkit_web_projects;
CREATE POLICY owner_insert ON public.ebkit_web_projects FOR INSERT TO authenticated WITH CHECK (user_id = auth.uid() AND mode = 'demo');
DROP POLICY owner_update ON public.ebkit_web_projects;
CREATE POLICY owner_update ON public.ebkit_web_projects FOR UPDATE TO authenticated USING (user_id = auth.uid() AND mode = 'demo') WITH CHECK (user_id = auth.uid() AND mode = 'demo');
DROP POLICY owner_delete ON public.ebkit_web_projects;
CREATE POLICY owner_delete ON public.ebkit_web_projects FOR DELETE TO authenticated USING (user_id = auth.uid() AND mode = 'demo');
