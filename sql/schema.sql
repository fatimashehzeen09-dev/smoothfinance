-- Dedicated Halal Chit Fund SQL records. Machine credential never reaches clients.
CREATE TABLE IF NOT EXISTS public.smoothfinance_records (
 id text PRIMARY KEY,
 payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
 version bigint NOT NULL CHECK (version > 0)
);
ALTER TABLE public.smoothfinance_records ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.smoothfinance_records FROM PUBLIC, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.smoothfinance_records TO anon;
CREATE POLICY hcf_backend_only ON public.smoothfinance_records
 FOR ALL TO anon
 USING ((select encode(sha256(convert_to(coalesce(current_setting('request.headers', true)::jsonb ->> 'x-hcf-backend-key', ''), 'UTF8')), 'hex')) = 'af2869fc619f0775a93a8eecc3ac5b0dfffeafd210ab1e85b96216eeca3f56af')
 WITH CHECK ((select encode(sha256(convert_to(coalesce(current_setting('request.headers', true)::jsonb ->> 'x-hcf-backend-key', ''), 'UTF8')), 'hex')) = 'af2869fc619f0775a93a8eecc3ac5b0dfffeafd210ab1e85b96216eeca3f56af');
NOTIFY pgrst, 'reload schema';
