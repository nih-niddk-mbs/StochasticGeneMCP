using CSV, DataFrames, JSON, SHA

length(ARGS) == 2 || error("Usage: compare_outputs.jl run-directory baseline-csv-directory")
job, baseline = ARGS
status = JSON.parsefile(joinpath(job,"status.json"))
status["state"] == "completed" || error("Run did not complete")
plan = JSON.parsefile(joinpath(job,"plan.json"))
worst = 0.0
for output in status["outputs"]
    actual = CSV.read(output,DataFrame)
    expected = CSV.read(joinpath(baseline,basename(output)),DataFrame)
    if :pair in propertynames(actual)
        expected = expected[in.(expected.pair,Ref(Set(actual.pair))),:]
    end
    @assert names(actual) == names(expected)
    @assert nrow(actual) == nrow(expected)
    for column in names(actual)
        if eltype(actual[!,column]) <: Number
            @assert all(isfinite,actual[!,column])
            global worst = max(worst,maximum(abs.(actual[!,column] .- expected[!,column])))
            @assert isapprox(actual[!,column],expected[!,column];atol=1e-10,rtol=1e-8)
        else
            @assert actual[!,column] == expected[!,column]
        end
    end
end
for item in plan["prepared"]["snapshots"]
    @assert bytes2hex(sha256(read(item["source"]))) == item["sha256"]
end
report = Dict("models"=>status["models_processed"],"csv_files"=>length(status["outputs"]),
    "maximum_difference"=>worst,"source_inputs_unchanged"=>true,"threads"=>status["threads"])
open(joinpath(job,"validation.json"),"w") do io
    JSON.print(io,report,2)
end
println(JSON.json(report))
