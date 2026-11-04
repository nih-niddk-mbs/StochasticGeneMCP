using StochasticGene, JSON, SHA, LinearAlgebra

const SG = StochasticGene
BLAS.set_num_threads(1)

function plain(x)
    x === nothing && return nothing
    x isa Union{AbstractString,Number,Bool} && return x
    x isa Union{Tuple,AbstractArray} && return [plain(v) for v in x]
    x isa Union{NamedTuple,AbstractDict} && return Dict(string(k)=>plain(v) for (k,v) in pairs(x))
    return string(x)
end

function fingerprint(expected_version, expected_revision)
    root = dirname(dirname(pathof(SG)))
    version = string(pkgversion(SG))
    version == expected_version ||
        error("StochasticGene $version differs from the configured version $expected_version; run setup or update the configuration")
    runtime = Dict{String,Any}("version"=>version, "source"=>root, "julia_version"=>string(VERSION))
    if expected_revision !== nothing
        revision = strip(read(`git -C $root rev-parse HEAD`, String))
        revision == expected_revision || error("StochasticGene revision differs from the configured pin; review and prepare a new run")
        isempty(strip(read(`git -C $root status --porcelain --untracked-files=no`, String))) ||
            error("StochasticGene checkout has uncommitted changes; use a clean pinned checkout")
        runtime["revision"] = revision
    end
    return runtime
end

function inside(file, root)
    relative = relpath(realpath(file), realpath(root))
    relative != ".." && !startswith(relative, ".." * string(Base.Filesystem.path_separator)) ||
        error("Input symlink escapes its approved folder: $file")
    return file
end

function inventory(folder, ratetype="ml")
    records, skipped = Dict[], String[]
    for (directory, dirs, files) in walkdir(folder; follow_symlinks=false)
        filter!(d -> !startswith(d,".") && !islink(joinpath(directory,d)), dirs)
        for file in files
            startswith(file,"info_") && endswith(file,".jld2") || continue
            length(records) < 256 || error("Prototype supports at most 256 info files per inspection")
            infofile = inside(joinpath(directory,file), folder)
            key = SG.get_key(file)
            occursin(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,199}$", key) || error("Unsupported result identifier: $key")
            info = SG.read_run_spec(infofile)
            ratefile = joinpath(directory,"rates_" * key * ".txt")
            exists = isfile(ratefile)
            exists && inside(ratefile,folder)
            shared = SG._analysis_is_shared_spec(info)
            coupling = get(info,:coupling,())
            coupled = get(info,:G,nothing) isa Tuple && length(coupling) >= 2
            usable = exists && coupled && !shared
            rates = exists ? SG.readrates(ratefile,SG.get_row(ratetype)) : Float64[]
            usable &= !isempty(rates) && all(isfinite,rates)
            !usable && push!(skipped, key * ": missing/invalid rates, single-unit model, or unsupported shared-dataset layout")
            summary = Dict{String,Any}("key"=>key, "info_file"=>infofile, "rate_file"=>ratefile,
                "rate_count"=>length(rates), "correlations_supported"=>usable, "shared_datasets"=>shared)
            for name in (:datatype,:gene,:cell,:datacond,:G,:R,:S,:insertstep,:transitions,
                         :coupling,:probfn,:hierarchical,:trace_specs,:decayrate,:nalleles)
                summary[string(name)] = plain(get(info,name,nothing))
            end
            declared = get(info,:fittedparam,Int[])
            summary["declared_fittedparam"] = plain(collect(declared)[1:min(256,length(declared))])
            summary["declared_fittedparam_count"] = length(declared)
            summary["parameter_note"] = "Declared spec indices; hierarchical copies are not expanded by this inspector."
            push!(records, Dict("summary"=>summary, "info"=>info, "usable"=>usable))
        end
    end
    isempty(records) && error("No info_*.jld2 files found in the selected folder")
    return records, skipped
end

function prepare(folder, job, settings, runtime)
    records, skipped = inventory(folder,settings["ratetype"])
    selected = filter(r -> r["usable"],records)
    isempty(selected) && error("No supported coupled result files in this folder")
    dt, maxlag = Float64(settings["frame_interval"]), Float64(settings["maxlag"])
    lags = collect(0.:dt:maxlag)
    SG._correlation_window_plan(lags, settings["trace_center"] ? Int(settings["window_length"]) : nothing,dt)
    results = joinpath(job,"results")
    mkdir(results)
    snapshots = Dict[]
    for record in selected
        for label in ("info_file","rate_file")
            source = record["summary"][label]
            target = joinpath(results,relpath(source,folder))
            mkpath(dirname(target))
            sha = bytes2hex(sha256(read(source)))
            cp(source,target)
            bytes2hex(sha256(read(target))) == sha || error("Input changed during preparation: $source")
            push!(snapshots,Dict("source"=>source,"snapshot"=>relpath(target,job),"sha256"=>sha))
        end
    end
    return Dict("runtime"=>runtime,"models"=>[r["summary"] for r in selected],
        "model_count"=>length(selected),"warnings"=>skipped,"snapshots"=>snapshots,
        "results_directory"=>results,"observables"=>["ON","reporter_number"],
        "centering"=>settings["trace_center"] ? "whole-trace-centered" : "global-centered",
        "normalization"=>"none; covariance columns end in _centered",
        "lag_convention"=>"positive lag: unit 1 at t against unit 2 at t+lag",
        "rate_row"=>settings["ratetype"], "source_modified"=>false)
end

function verify_plan(job, plan)
    prepared = plan["prepared"]
    prepared["runtime"]["version"] == plan["expected_version"] || error("Invalid plan version")
    for item in prepared["snapshots"]
        file = inside(joinpath(job,item["snapshot"]),job)
        bytes2hex(sha256(read(file))) == item["sha256"] || error("Prepared input changed after approval")
    end
    return prepared
end

function compute(job, plan)
    prepared, settings = plan["prepared"], plan["settings"]
    verify_plan(job, plan)
    results = inside(prepared["results_directory"],job)
    dt = Float64(settings["frame_interval"])
    elapsed = @elapsed nmodels = SG.write_correlation_functions_key(results;
        ratetype=settings["ratetype"], lags=collect(0.:dt:Float64(settings["maxlag"])),
        trace_center=settings["trace_center"], window_lengths=Int(settings["window_length"]),
        window_interval=dt, threaded=true, max_concurrent_jobs=Int(settings["threads"]))
    nmodels == prepared["model_count"] || error("Not all prepared models were processed")
    outputs = String[]
    for (directory,_,files) in walkdir(results), file in files
        startswith(file,"crosscorrelation") && endswith(file,".csv") && push!(outputs,joinpath(directory,file))
    end
    length(outputs) == 2nmodels || error("Unexpected correlation output count")
    return Dict("models_processed"=>nmodels,"elapsed_seconds"=>elapsed,
        "outputs"=>sort(outputs),"results_directory"=>results,"threads"=>Threads.nthreads())
end

function table(df)
    return [Dict{String,Any}(n => plain(df[i, n]) for n in names(df)) for i in 1:size(df, 1)]
end

function prepare_fit(data_file, runtime)
    h = vec(SG.readfile(data_file)[:,1])
    all(isfinite, h) && all(>=(0), h) || error("Histogram must contain finite, non-negative counts")
    length(h) >= 4 || error("Histogram needs at least 4 bins (RNA copy numbers 0 to 3)")
    cells = sum(h)
    cells > 0 || error("Histogram is empty")
    return Dict("runtime"=>runtime, "bins"=>length(h), "cells"=>cells,
        "mean_rna"=>sum((0:length(h)-1) .* h) / cells, "max_rna"=>length(h) - 1)
end

function compute_fit_rna(job, plan)
    prepared, s = plan["prepared"], plan["settings"]
    verify_plan(job, plan)
    file = inside(joinpath(job, prepared["snapshots"][1]["snapshot"]), job)
    halflife = s["halflife"] === nothing ? nothing : Float64(s["halflife"])
    elapsed = @elapsed result = SG.fit_rna(file; G=Int(s["G"]), R=Int(s["R"]), S=Int(s["S"]),
        insertstep=Int(s["insertstep"]), name=String(s["name"]), halflife=halflife,
        outdir=joinpath(job, "fit"), samplesteps=Int(s["samplesteps"]), nchains=Int(s["nchains"]),
        maxtime=Float64(s["maxtime"]))
    outputs = sort([joinpath(d, f) for (d, _, fs) in walkdir(result.resultdir) for f in fs])
    return Dict("elapsed_seconds"=>elapsed, "results_directory"=>result.resultdir,
        "rates"=>table(result.rates), "outputs"=>outputs, "threads"=>Threads.nthreads())
end

function describe(folder)
    df = SG.fit_summary(folder)
    bursts = sort([f for f in readdir(folder) if startswith(f, "burst_")])
    return Dict("results_directory"=>folder, "rates"=>table(df), "burst_files"=>bursts)
end

function main(request_file, response_file)
    request = JSON.parsefile(request_file)
    request["protocol"] == 1 || error("Unsupported request protocol")
    job = realpath(request["job_dir"])
    inside(request_file,job)
    dirname(abspath(response_file)) == job || error("Response must stay in its run directory")
    runtime = fingerprint(request["expected_version"], get(request, "expected_revision", nothing))
    operation = request["operation"]
    if operation == "inspect"
        records, skipped = inventory(realpath(request["folder"]))
        return Dict("runtime"=>runtime,"models"=>[r["summary"] for r in records],"warnings"=>skipped)
    elseif operation == "prepare"
        return prepare(realpath(request["folder"]),job,request["settings"],runtime)
    elseif operation == "prepare_fit"
        return prepare_fit(inside(request["data_file"], job), runtime)
    elseif operation == "describe"
        return describe(realpath(request["folder"]))
    elseif operation == "compute"
        plan = request["plan"]
        kind = get(plan, "kind", "correlations")
        kind == "correlations" && return compute(job, plan)
        kind == "fit_rna" && return compute_fit_rna(job, plan)
        error("Unsupported plan kind: $kind")
    end
    error("Unsupported operation: $operation")
end

length(ARGS) == 2 || error("Usage: runner.jl request.json response.json")
response = try
    Dict("ok"=>true,"result"=>main(ARGS...))
catch error
    showerror(stderr,error,catch_backtrace())
    Dict("ok"=>false,"error"=>sprint(showerror,error))
end
temporary = ARGS[2] * ".new"
open(temporary,"w") do io
    JSON.print(io,response,2)
end
mv(temporary,ARGS[2]; force=true)
response["ok"] || exit(1)
