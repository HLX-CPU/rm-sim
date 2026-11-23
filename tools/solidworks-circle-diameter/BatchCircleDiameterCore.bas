Attribute VB_Name = "BatchCircleDiameterCore"
Option Explicit

Private Const MM_PER_METER As Double = 1000#
Private Const TOLERANCE_M As Double = 0.00000001
Private Const LINK_PREFIX As String = "BatchCircleDiameter_"
Private Const LINK_OK As Long = 1
Private Const BATCH_ERROR As Long = vbObjectError + 1842

Private Enum SkipReason
    SkipNone = 0
    SkipOutsideGroup = 1
    SkipExistingRadius = 2
    SkipLockedDimension = 3
    SkipConflictingLink = 4
    SkipRelation = 5
End Enum

Private Type CircleInfo
    Segment As SldWorks.SketchSegment
    Arc As SldWorks.SketchArc
    Diameter As SldWorks.DisplayDimension
    Candidate As Boolean
    Reason As SkipReason
    Parent As Long
    OriginalRelations As Long
    AppliedRelations As Long
    HadDiameter As Boolean
    OriginalLinkedText As String
    OldDimensionM As Double
    OriginalDimensionName As String
    TouchedDriver As Boolean
    CreatedDiameter As Boolean
    TriedLink As Boolean
    OldRadius As Double
    OldX As Double
    OldY As Double
    OldZ As Double
End Type

Private lastCircles() As CircleInfo
Private lastCount As Long
Private lastModel As SldWorks.ModelDoc2
Private lastSketch As SldWorks.Sketch
Private lastGroupName As String
Private lastDiameterM As Double
Private lastUpdateStamp As Long

Public Sub undoLastBatch()
    MsgBox DescribeResult(UndoBatchCircleDiameter(Application.SldWorks)), vbInformation, DialogTitle()
End Sub

Public Sub main()
    RunBatchCircleDiameter Application.SldWorks
End Sub

Public Sub RunBatchCircleDiameter(ByVal swApp As SldWorks.SldWorks)
    Dim result As String
    result = ExecuteBatch(swApp, 0#, True)
    If result <> "CANCELLED" Then MsgBox DescribeResult(result), vbInformation, DialogTitle()
End Sub

Public Function ApplyBatchCircleDiameter(ByVal swApp As SldWorks.SldWorks, ByVal diameterMm As Double) As String
    ApplyBatchCircleDiameter = ExecuteBatch(swApp, diameterMm, False)
End Function

Public Function UndoBatchCircleDiameter(ByVal swApp As SldWorks.SldWorks) As String
    Dim model As SldWorks.ModelDoc2, sketch As SldWorks.Sketch
    Dim oldScale As Boolean, settingsRead As Boolean
    Dim result As String
    On Error GoTo Failed
    result = "UNDO_NO_BATCH"
    If lastCount = 0 Then GoTo Cleanup
    result = "UNDO_WRONG_SKETCH"
    Set model = swApp.ActiveDoc
    If model Is Nothing Then GoTo Cleanup
    If swApp.IsSame(model, lastModel) <> swObjectSame Then GoTo Cleanup
    Set sketch = model.SketchManager.ActiveSketch
    If sketch Is Nothing Then GoTo Cleanup
    If Not sketch Is lastSketch Then GoTo Cleanup
    result = "UNDO_STALE"
    If model.GetUpdateStamp <> lastUpdateStamp Then GoTo Stale
    If sketch.IsSketchEditable = False Or sketch.GetAutomaticSolve = False Then GoTo Cleanup
    If model.IsOpenedReadOnly Then GoTo Cleanup
    If Not LastBatchStillCurrent(model) Then GoTo Stale

    oldScale = (swApp.GetUserPreferenceToggle(swScaleSketchOnFirstDimension) <> False)
    settingsRead = True
    SetToggle swApp, swScaleSketchOnFirstDimension, False
    If RestoreBatch(model, sketch, lastCircles, lastCount, lastGroupName) Then
        result = "UNDO_OK"
    Else
        result = "ERROR_CHECK_UNDO " & "Could not verify the restored sketch."
    End If
    ShowSelection model, lastCircles, lastCount, False
Stale:
    ClearLastBatch
    GoTo Cleanup
Failed:
    result = "ERROR_CHECK_UNDO " & Err.Description
    ClearLastBatch
    Resume Cleanup
Cleanup:
    On Error Resume Next
    If settingsRead Then swApp.SetUserPreferenceToggle swScaleSketchOnFirstDimension, oldScale
    UndoBatchCircleDiameter = result
End Function

Private Function ExecuteBatch(ByVal swApp As SldWorks.SldWorks, ByVal diameterMm As Double, ByVal interactive As Boolean) As String
    Dim model As SldWorks.ModelDoc2
    Dim assembly As SldWorks.AssemblyDoc
    Dim editTarget As Object
    Dim sketch As SldWorks.Sketch
    Dim docExt As SldWorks.ModelDocExtension
    Dim circles() As CircleInfo
    Dim drivers() As Long
    Dim circleCount As Long, candidateCount As Long, eligibleCount As Long
    Dim index As Long, root As Long, firstDriver As Long
    Dim entered As String, groupName As String, conflict As String
    Dim display As SldWorks.DisplayDimension
    Dim dimension As SldWorks.Dimension
    Dim oldInput As Boolean, oldScale As Boolean
    Dim oldPrompt As Boolean, oldDriven As Boolean
    Dim settingsRead As Boolean, undoStarted As Boolean, changed As Boolean
    Dim undoReady As Boolean, restored As Boolean, result As String, errorText As String
    Dim status As Long, newDimensions As Long
    Dim diameterM As Double, skipDetails As String

    On Error GoTo Failed
    Set model = swApp.ActiveDoc
    result = "NO_SKETCH"
    If model Is Nothing Then GoTo Cleanup
    If model.GetType <> swDocPART And model.GetType <> swDocASSEMBLY Then GoTo Cleanup
    If model.GetType = swDocASSEMBLY Then
        Set assembly = model
        Set editTarget = assembly.GetEditTarget
        If Not editTarget Is Nothing Then
            If swApp.IsSame(model, editTarget) <> swObjectSame Then
                result = "IN_CONTEXT_EDIT"
                GoTo Cleanup
            End If
        End If
    End If
    Set sketch = model.SketchManager.ActiveSketch
    If sketch Is Nothing Then GoTo Cleanup
    If sketch.Is3D Then GoTo Cleanup
    If model.GetConfigurationCount <> 1 Then
        result = "MULTI_CONFIGURATION"
        GoTo Cleanup
    End If
    If sketch.IsSketchEditable = False Or sketch.GetAutomaticSolve = False Then
        result = "SKETCH_UNAVAILABLE"
        GoTo Cleanup
    End If
    If model.IsOpenedReadOnly Then
        result = "SKETCH_UNAVAILABLE"
        GoTo Cleanup
    End If
    status = sketch.GetConstrainedStatus
    If status <> swUnderConstrained And status <> swFullyConstrained Then
        result = "SKETCH_UNAVAILABLE"
        GoTo Cleanup
    End If

    circleCount = ReadCircles(sketch, circles, candidateCount)
    If candidateCount = 0 Then
        result = "NO_TARGETS"
        GoTo Cleanup
    End If

    InspectRelations swApp, model, sketch, circles, circleCount
    ReDim drivers(1 To circleCount)
    FindDrivers circles, circleCount, drivers
    groupName = ReusableGroup(model, circles, circleCount, drivers)
    For index = 1 To circleCount
        root = GroupRoot(circles, index)
        If circles(index).Candidate And circles(root).Reason = SkipNone Then
            eligibleCount = eligibleCount + 1
        End If
    Next index
    skipDetails = FormatSkipDetails(circles, circleCount)
    ShowSelection model, circles, circleCount, True
    If eligibleCount = 0 Then
        result = "NO_ELIGIBLE|" & skipDetails
        GoTo Cleanup
    End If

    If interactive Then
        entered = InputBox(DiameterPrompt(candidateCount, eligibleCount), DialogTitle())
        If Len(entered) = 0 Then
            result = "CANCELLED"
            GoTo Cleanup
        End If
        If Not ReadPositiveNumber(entered, diameterMm) Then
            result = "INVALID_INPUT"
            GoTo Cleanup
        End If
    End If
    If diameterMm <= 0# Then
        result = "INVALID_INPUT"
        GoTo Cleanup
    End If
    diameterM = diameterMm / MM_PER_METER
    If diameterM <= 0# Then
        result = "INVALID_INPUT"
        GoTo Cleanup
    End If

    If Len(groupName) = 0 Then
        groupName = LINK_PREFIX & Format$(Now, "yyyymmdd_hhnnss") & "_" & Format$(Timer * 10000#, "0")
    End If
    Set docExt = model.Extension
    oldInput = (swApp.GetUserPreferenceToggle(swInputDimValOnCreate) <> False)
    oldScale = (swApp.GetUserPreferenceToggle(swScaleSketchOnFirstDimension) <> False)
    oldPrompt = (swApp.GetUserPreferenceToggle(swSketchOverdefiningDimsPromptToSetState) <> False)
    oldDriven = (swApp.GetUserPreferenceToggle(swSketchOverdefiningDimsSetDrivenByDefault) <> False)
    settingsRead = True
    SetToggle swApp, swInputDimValOnCreate, False
    SetToggle swApp, swScaleSketchOnFirstDimension, False
    SetToggle swApp, swSketchOverdefiningDimsPromptToSetState, False
    SetToggle swApp, swSketchOverdefiningDimsSetDrivenByDefault, True

    docExt.StartRecordingUndoObject
    undoStarted = True
    For root = 1 To circleCount
        index = drivers(root)
        If index > 0 And circles(root).Reason = SkipNone Then
            If firstDriver = 0 Then firstDriver = index
            circles(index).TouchedDriver = True
            Set display = circles(index).Diameter
            If display Is Nothing Then
                Set display = AddDiameter(swApp, model, sketch, circles(index))
                If display Is Nothing Then Err.Raise BATCH_ERROR, , "Could not add a diameter dimension."
                changed = True
                Set circles(index).Diameter = display
                circles(index).CreatedDiameter = True
                circles(index).OldDimensionM = circles(index).OldRadius * 2#
                newDimensions = newDimensions + 1
                Set dimension = display.GetDimension2(0)
                If dimension Is Nothing Then Err.Raise BATCH_ERROR, , "The new diameter has no dimension object."
                If dimension.DrivenState <> swDimensionDriving Then Err.Raise BATCH_ERROR, , "The sketch solver only allowed a reference diameter."
            End If
            If display.IsLinked = False Then
                circles(index).TriedLink = True
                changed = True
                status = display.SetLinkedText(groupName)
                If status <> LINK_OK Then Err.Raise BATCH_ERROR, , "Link Values refused a diameter (" & status & ")."
            End If
            If StrComp(display.GetLinkedText, groupName, vbTextCompare) <> 0 Then
                Err.Raise BATCH_ERROR, , "The diameter did not join the shared value."
            End If
        End If
    Next root

    Set dimension = circles(firstDriver).Diameter.GetDimension2(0)
    If Abs(dimension.SystemValue - diameterM) > TOLERANCE_M Then
        changed = True
        status = dimension.SetSystemValue3(diameterM, swSetValue_InThisConfiguration, Empty)
        If status <> swSetValue_Successful Then Err.Raise BATCH_ERROR, , "Cannot set the shared diameter (" & status & ")."
    End If
    If Not RebuildActiveSketch(model, sketch) Then Err.Raise BATCH_ERROR, , "The model rebuild failed."
    status = sketch.GetConstrainedStatus
    If status <> swUnderConstrained And status <> swFullyConstrained Then Err.Raise BATCH_ERROR, , "The sketch solver reported a conflict."
    conflict = CheckGeometry(circles, circleCount, diameterM, True)
    If Len(conflict) > 0 Then Err.Raise BATCH_ERROR, , conflict
    result = "OK " & eligibleCount & "|" & candidateCount & "|" & newDimensions & "|" & CStr(diameterMm) & "|" & groupName & "|" & skipDetails
    GoTo Cleanup

Failed:
    errorText = Err.Description
    result = "ERROR " & errorText
    Resume Cleanup

Cleanup:
    On Error Resume Next
    If Len(errorText) > 0 And changed Then
        restored = RestoreBatch(model, sketch, circles, circleCount, groupName)
        If restored Then
            result = "ERROR_ROLLED_BACK " & errorText
        Else
            result = "ERROR_CHECK_UNDO " & errorText
        End If
    End If
    If undoStarted Then undoReady = (docExt.FinishRecordingUndoObject2(DialogTitle(), restored) <> False)
    If Len(errorText) = 0 And changed And Not undoReady Then
        result = result & "|UNDO_UNAVAILABLE"
    End If
    If settingsRead Then
        swApp.SetUserPreferenceToggle swInputDimValOnCreate, oldInput
        swApp.SetUserPreferenceToggle swScaleSketchOnFirstDimension, oldScale
        swApp.SetUserPreferenceToggle swSketchOverdefiningDimsPromptToSetState, oldPrompt
        swApp.SetUserPreferenceToggle swSketchOverdefiningDimsSetDrivenByDefault, oldDriven
    End If
    If candidateCount > 0 Then ShowSelection model, circles, circleCount, False
    If Len(errorText) = 0 And changed Then
        If Not SaveLastBatch(model, sketch, circles, circleCount, groupName, diameterM) Then result = result & "|BATCH_UNDO_UNAVAILABLE"
    ElseIf Len(errorText) > 0 And changed And Not restored Then
        ClearLastBatch
    End If
    ExecuteBatch = result
End Function

Private Function SaveLastBatch(ByVal model As SldWorks.ModelDoc2, ByVal sketch As SldWorks.Sketch, ByRef circles() As CircleInfo, ByVal count As Long, ByVal groupName As String, ByVal diameterM As Double) As Boolean
    Dim index As Long
    On Error GoTo Failed
    ClearLastBatch
    ReDim lastCircles(1 To count)
    For index = 1 To count
        lastCircles(index) = circles(index)
        lastCircles(index).AppliedRelations = circles(index).Segment.GetRelationsCount
    Next index
    Set lastModel = model
    Set lastSketch = sketch
    lastGroupName = groupName
    lastDiameterM = diameterM
    lastUpdateStamp = model.GetUpdateStamp
    lastCount = count
    SaveLastBatch = True
    Exit Function
Failed:
    ClearLastBatch
End Function

Private Sub ClearLastBatch()
    lastCount = 0
    Erase lastCircles
    Set lastModel = Nothing
    Set lastSketch = Nothing
End Sub

Private Function LastBatchStillCurrent(ByVal model As SldWorks.ModelDoc2) As Boolean
    Dim index As Long
    Dim display As SldWorks.DisplayDimension
    Dim dimension As SldWorks.Dimension
    Dim allowedNames As Object
    If Len(CheckGeometry(lastCircles, lastCount, lastDiameterM, True)) > 0 Then Exit Function
    Set allowedNames = CreateObject("Scripting.Dictionary")
    allowedNames.CompareMode = vbTextCompare
    For index = 1 To lastCount
        If lastCircles(index).Segment.GetRelationsCount <> lastCircles(index).AppliedRelations Then Exit Function
        If lastCircles(index).TouchedDriver Then
            Set display = lastCircles(index).Diameter
            If display.IsLinked = False Then Exit Function
            If StrComp(display.GetLinkedText, lastGroupName, vbTextCompare) <> 0 Then Exit Function
            Set dimension = display.GetDimension2(0)
            If dimension Is Nothing Then Exit Function
            If dimension.DrivenState <> swDimensionDriving Then Exit Function
            If Abs(dimension.SystemValue - lastDiameterM) > TOLERANCE_M Then Exit Function
            allowedNames(dimension.FullName) = True
        End If
    Next index
    LastBatchStillCurrent = Not GroupHasOtherMembers(model, lastGroupName, allowedNames)
End Function

Private Function ReadCircles(ByVal sketch As SldWorks.Sketch, ByRef circles() As CircleInfo, ByRef candidateCount As Long) As Long
    Dim segments As Variant, item As Variant
    Dim segment As SldWorks.SketchSegment
    Dim arc As SldWorks.SketchArc
    Dim center As SldWorks.SketchPoint
    Dim count As Long
    segments = sketch.GetSketchSegments
    If Not IsArray(segments) Then Exit Function
    ReDim circles(1 To UBound(segments) - LBound(segments) + 1)
    For Each item In segments
        Set segment = item
        If Not segment Is Nothing Then
            If segment.GetType = swSketchARC Then
                Set arc = segment
                If arc.IsCircle = 1 Then
                    count = count + 1
                    Set circles(count).Segment = segment
                    Set circles(count).Arc = arc
                    circles(count).Parent = count
                    circles(count).Candidate = (segment.Status = swUnderConstrained)
                    If circles(count).Candidate Then candidateCount = candidateCount + 1
                    circles(count).OldRadius = arc.GetRadius
                    circles(count).OriginalRelations = segment.GetRelationsCount
                    Set center = arc.GetCenterPoint2
                    circles(count).OldX = center.X
                    circles(count).OldY = center.Y
                    circles(count).OldZ = center.Z
                End If
            End If
        End If
    Next item
    ReadCircles = count
End Function

Private Sub InspectRelations(ByVal swApp As SldWorks.SldWorks, ByVal model As SldWorks.ModelDoc2, ByVal sketch As SldWorks.Sketch, ByRef circles() As CircleInfo, ByVal count As Long)
    Dim index As Long, other As Long, relationType As Long
    Dim relations As Variant, item As Variant, entities As Variant, entity As Variant
    Dim relation As SldWorks.SketchRelation
    Dim display As SldWorks.DisplayDimension
    Dim dimension As SldWorks.Dimension
    For index = 1 To count
        If circles(index).Candidate Then
            relations = circles(index).Segment.GetRelations
            If IsArray(relations) Then
                For Each item In relations
                    Set relation = item
                    If Not relation Is Nothing Then
                        If relation.Suppressed = False Then
                            relationType = relation.GetRelationType
                            Select Case relationType
                                Case swConstraintType_SAMELENGTH, swConstraintType_CORADIAL
                                    entities = relation.GetEntities
                                    If IsArray(entities) Then
                                        For Each entity In entities
                                            other = CircleIndex(swApp, sketch, circles, count, entity)
                                            If other = 0 Then
                                                circles(index).Reason = SkipOutsideGroup
                                            Else
                                                circles(GroupRoot(circles, other)).Parent = GroupRoot(circles, index)
                                            End If
                                        Next entity
                                    Else
                                        circles(index).Reason = SkipOutsideGroup
                                    End If
                                Case swConstraintType_DIAMETER, swConstraintType_RADIUS
                                    Set display = relation.GetDisplayDimension
                                    If display Is Nothing Then
                                        circles(index).Reason = SkipLockedDimension
                                    Else
                                        Set dimension = display.GetDimension2(0)
                                        If dimension Is Nothing Then
                                            circles(index).Reason = SkipLockedDimension
                                        ElseIf dimension.DrivenState = swDimensionDriving Then
                                            If relationType <> swConstraintType_DIAMETER Then
                                                circles(index).Reason = SkipExistingRadius
                                            ElseIf dimension.ReadOnly Or dimension.IsDesignTableDimension Then
                                                circles(index).Reason = SkipLockedDimension
                                            ElseIf HasDrivingEquation(model, dimension) Then
                                                circles(index).Reason = SkipLockedDimension
                                            ElseIf Not circles(index).Diameter Is Nothing Then
                                                circles(index).Reason = SkipLockedDimension
                                            Else
                                                Set circles(index).Diameter = display
                                                circles(index).HadDiameter = True
                                                circles(index).OldDimensionM = dimension.SystemValue
                                                circles(index).OriginalDimensionName = dimension.Name
                                                If display.IsLinked Then circles(index).OriginalLinkedText = display.GetLinkedText
                                            End If
                                        End If
                                    End If
                                Case swConstraintType_CONCENTRIC, swConstraintType_DISTANCE, swConstraintType_HORIZONTAL, swConstraintType_VERTICAL
                                    ' The solver and the center check validate positional relations.
                                Case Else
                                    ' A tangency, fixed circle, pattern, or external reference can control size or move other geometry.
                                    circles(index).Reason = SkipRelation
                            End Select
                        End If
                    End If
                Next item
            End If
        End If
    Next index
End Sub

Private Function CircleIndex(ByVal swApp As SldWorks.SldWorks, ByVal sketch As SldWorks.Sketch, ByRef circles() As CircleInfo, ByVal count As Long, ByVal entity As Object) As Long
    Dim relationSegment As SldWorks.SketchSegment
    Dim owner As SldWorks.Sketch
    Dim relationId As Variant, candidateId As Variant
    Dim index As Long

    If entity Is Nothing Then Exit Function
    If Not TypeOf entity Is SldWorks.SketchSegment Then Exit Function
    Set relationSegment = entity
    If relationSegment.GetType <> swSketchARC Then Exit Function
    Set owner = relationSegment.GetSketch
    If owner Is Nothing Then Exit Function
    If swApp.IsSame(owner, sketch) <> swObjectSame Then Exit Function

    ' Relation and sketch enumeration can return different COM proxies for the same segment.
    relationId = relationSegment.GetID
    If Not IsArray(relationId) Then Exit Function
    For index = 1 To count
        candidateId = circles(index).Segment.GetID
        If IsArray(candidateId) Then
            If candidateId(0) = relationId(0) And candidateId(1) = relationId(1) Then
                CircleIndex = index
                Exit Function
            End If
        End If
    Next index
End Function

Private Function GroupRoot(ByRef circles() As CircleInfo, ByVal index As Long) As Long
    Dim root As Long
    root = index
    Do While circles(root).Parent <> root
        root = circles(root).Parent
    Loop
    circles(index).Parent = root
    GroupRoot = root
End Function

Private Sub FindDrivers(ByRef circles() As CircleInfo, ByVal count As Long, ByRef drivers() As Long)
    Dim index As Long, root As Long
    For index = 1 To count
        root = GroupRoot(circles, index)
        If Not circles(index).Candidate Then circles(root).Reason = SkipOutsideGroup
        If circles(index).Reason <> SkipNone Then circles(root).Reason = circles(index).Reason
        If Not circles(index).Diameter Is Nothing Then
            If drivers(root) > 0 Then circles(root).Reason = SkipLockedDimension
            drivers(root) = index
        End If
    Next index
    For index = 1 To count
        root = GroupRoot(circles, index)
        If drivers(root) = 0 And circles(index).Candidate Then drivers(root) = index
    Next index
End Sub

Private Function HasDrivingEquation(ByVal model As SldWorks.ModelDoc2, ByVal dimension As SldWorks.Dimension) As Boolean
    Dim equations As SldWorks.EquationMgr
    Dim index As Long, equalsAt As Long
    Dim formula As String, lhs As String, dimName As String
    Set equations = model.GetEquationMgr
    If equations Is Nothing Then Exit Function
    dimName = dimension.FullName
    For index = 0 To equations.GetCount - 1
        formula = equations.Equation(index)
        equalsAt = InStr(1, formula, "=")
        If equalsAt > 2 Then
            lhs = Trim$(Left$(formula, equalsAt - 1))
            If Len(lhs) > 2 And Left$(lhs, 1) = """" And Right$(lhs, 1) = """" Then
                lhs = Mid$(lhs, 2, Len(lhs) - 2)
                If StrComp(dimName, lhs, vbTextCompare) = 0 Or StrComp(Left$(dimName, Len(lhs) + 1), lhs & "@", vbTextCompare) = 0 Then
                    HasDrivingEquation = True
                    Exit Function
                End If
            End If
        End If
    Next index
End Function

Private Function ReusableGroup(ByVal model As SldWorks.ModelDoc2, ByRef circles() As CircleInfo, ByVal count As Long, ByRef drivers() As Long) As String
    Dim root As Long, index As Long, groupName As String, existing As String
    Dim conflicting As Boolean, allowedNames As Object
    Dim display As SldWorks.DisplayDimension, dimension As SldWorks.Dimension
    Set allowedNames = CreateObject("Scripting.Dictionary")
    allowedNames.CompareMode = vbTextCompare
    For root = 1 To count
        index = drivers(root)
        If index > 0 And circles(root).Reason = SkipNone Then
            Set display = circles(index).Diameter
            If Not display Is Nothing Then
                If display.IsLinked Then
                    existing = display.GetLinkedText
                    If Left$(existing, Len(LINK_PREFIX)) <> LINK_PREFIX Then
                        circles(root).Reason = SkipConflictingLink
                    Else
                        If Len(groupName) = 0 Then groupName = existing
                        If StrComp(existing, groupName, vbTextCompare) <> 0 Then conflicting = True
                        Set dimension = display.GetDimension2(0)
                        allowedNames(dimension.FullName) = True
                    End If
                End If
            End If
        End If
    Next root
    If Len(groupName) = 0 Then Exit Function
    If Not conflicting Then conflicting = GroupHasOtherMembers(model, groupName, allowedNames)
    If conflicting Then
        For root = 1 To count
            index = drivers(root)
            If index > 0 Then
                Set display = circles(index).Diameter
                If Not display Is Nothing Then
                    If display.IsLinked Then circles(root).Reason = SkipConflictingLink
                End If
            End If
        Next root
    Else
        ReusableGroup = groupName
    End If
End Function

Private Function GroupHasOtherMembers(ByVal model As SldWorks.ModelDoc2, ByVal groupName As String, ByVal allowedNames As Object) As Boolean
    Dim feature As SldWorks.Feature
    Dim display As SldWorks.DisplayDimension
    Dim dimension As SldWorks.Dimension
    Set feature = model.FirstFeature
    Do Until feature Is Nothing
        Set display = feature.GetFirstDisplayDimension
        Do Until display Is Nothing
            If display.IsLinked Then
                If StrComp(display.GetLinkedText, groupName, vbTextCompare) = 0 Then
                    Set dimension = display.GetDimension2(0)
                    If dimension Is Nothing Then
                        GroupHasOtherMembers = True
                    ElseIf Not allowedNames.Exists(dimension.FullName) Then
                        GroupHasOtherMembers = True
                    End If
                    If GroupHasOtherMembers Then Exit Function
                End If
            End If
            Set display = feature.GetNextDisplayDimension(display)
        Loop
        Set feature = feature.GetNextFeature
    Loop
End Function

Private Function AddDiameter(ByVal swApp As SldWorks.SldWorks, ByVal model As SldWorks.ModelDoc2, ByVal sketch As SldWorks.Sketch, ByRef targetCircle As CircleInfo) As SldWorks.DisplayDimension
    Dim math As SldWorks.MathUtility, origin As SldWorks.MathPoint
    Dim xform As SldWorks.MathTransform, position As Variant
    Dim coordinates(0 To 2) As Double, offset As Double
    offset = targetCircle.OldRadius * 1.7
    coordinates(0) = targetCircle.OldX + offset
    coordinates(1) = targetCircle.OldY + offset * 0.7
    coordinates(2) = targetCircle.OldZ
    Set math = swApp.GetMathUtility
    Set xform = sketch.ModelToSketchTransform
    Set origin = math.CreatePoint(coordinates)
    Set origin = origin.MultiplyTransform(xform.Inverse)
    position = origin.ArrayData
    model.ClearSelection2 True
    If targetCircle.Segment.Select4(False, Nothing) Then
        Set AddDiameter = model.AddDiameterDimension2(position(0), position(1), position(2))
    End If
End Function

Private Function CheckGeometry(ByRef circles() As CircleInfo, ByVal count As Long, ByVal diameterM As Double, ByVal applied As Boolean) As String
    Dim index As Long, root As Long, expectedRadius As Double
    Dim center As SldWorks.SketchPoint
    For index = 1 To count
        root = GroupRoot(circles, index)
        expectedRadius = circles(index).OldRadius
        If applied And circles(index).Candidate And circles(root).Reason = SkipNone Then expectedRadius = diameterM / 2#
        If Abs(circles(index).Arc.GetRadius - expectedRadius) > TOLERANCE_M Then
            CheckGeometry = "The solved circle diameter differs from the expected value."
            Exit Function
        End If
        Set center = circles(index).Arc.GetCenterPoint2
        If Abs(center.X - circles(index).OldX) > TOLERANCE_M Or Abs(center.Y - circles(index).OldY) > TOLERANCE_M Or Abs(center.Z - circles(index).OldZ) > TOLERANCE_M Then
            CheckGeometry = "The sketch solver moved a circle center."
            Exit Function
        End If
    Next index
End Function

Private Function RebuildActiveSketch(ByVal model As SldWorks.ModelDoc2, ByVal sketch As SldWorks.Sketch) As Boolean
    Dim sketchFeature As SldWorks.Feature
    Dim reentered As SldWorks.Sketch
    Dim rebuilt As Boolean
    On Error GoTo Failed
    Set sketchFeature = sketch
    rebuilt = (model.EditRebuild3 <> False)
    Set reentered = model.SketchManager.ActiveSketch
    If reentered Is Nothing Then
        model.ClearSelection2 True
        If sketchFeature.Select2(False, 0) = False Then Exit Function
        model.EditSketch
        Set reentered = model.SketchManager.ActiveSketch
    End If
    RebuildActiveSketch = rebuilt And (reentered Is sketch)
Failed:
End Function

Private Function RestoreBatch(ByVal model As SldWorks.ModelDoc2, ByVal sketch As SldWorks.Sketch, ByRef circles() As CircleInfo, ByVal count As Long, ByVal groupName As String) As Boolean
    Dim activeSketch As SldWorks.Sketch
    Dim dimension As SldWorks.Dimension
    Dim index As Long, ok As Boolean, status As Long
    On Error GoTo Failed
    Set activeSketch = model.SketchManager.ActiveSketch
    If activeSketch Is Nothing Then Exit Function
    If Not activeSketch Is sketch Then Exit Function
    ok = True
    For index = 1 To count
        If circles(index).TriedLink Then
            If Not RemoveBatchLink(circles(index), groupName) Then ok = False
        End If
    Next index
    For index = 1 To count
        If circles(index).TouchedDriver And (circles(index).HadDiameter Or circles(index).CreatedDiameter) Then
            If Not RestoreOriginalValue(circles(index)) Then ok = False
        End If
    Next index
    ' A free circle keeps the solved radius when its driving dimension is removed.
    If Not RebuildActiveSketch(model, sketch) Then ok = False
    For index = 1 To count
        If circles(index).CreatedDiameter Then
            If Not RemoveNewDiameter(model, circles(index).Diameter) Then ok = False
        End If
    Next index
    If Not RebuildActiveSketch(model, sketch) Then ok = False
    For index = 1 To count
        If circles(index).TouchedDriver And circles(index).HadDiameter Then
            Set dimension = circles(index).Diameter.GetDimension2(0)
            If StrComp(dimension.Name, circles(index).OriginalDimensionName, vbBinaryCompare) <> 0 Then
                dimension.Name = circles(index).OriginalDimensionName
            End If
        End If
    Next index
    status = sketch.GetConstrainedStatus
    If status <> swUnderConstrained And status <> swFullyConstrained Then ok = False
    RestoreBatch = ok And Len(CheckGeometry(circles, count, 0#, False)) = 0 And OriginalDimensionsRestored(circles, count)
Failed:
End Function

Private Function RemoveBatchLink(ByRef targetCircle As CircleInfo, ByVal groupName As String) As Boolean
    Dim display As SldWorks.DisplayDimension
    On Error GoTo Failed
    Set display = targetCircle.Diameter
    If display.IsLinked Then
        If StrComp(display.GetLinkedText, groupName, vbTextCompare) <> 0 Then Exit Function
        If display.Unlink <> LINK_OK Then Exit Function
    End If
    RemoveBatchLink = (display.IsLinked = False)
Failed:
End Function

Private Function RemoveNewDiameter(ByVal model As SldWorks.ModelDoc2, ByVal display As SldWorks.DisplayDimension) As Boolean
    Dim annotation As SldWorks.Annotation
    On Error GoTo Failed
    Set annotation = display.GetAnnotation
    If annotation Is Nothing Then Exit Function
    model.ClearSelection2 True
    If annotation.Select3(False, Nothing) = False Then Exit Function
    If model.SelectionManager.GetSelectedObjectCount2(-1) <> 1 Then Exit Function
    RemoveNewDiameter = (model.Extension.DeleteSelection2(0) <> False)
Failed:
End Function

Private Function RestoreOriginalValue(ByRef targetCircle As CircleInfo) As Boolean
    Dim display As SldWorks.DisplayDimension
    Dim dimension As SldWorks.Dimension
    Dim currentLink As String
    On Error GoTo Failed
    Set display = targetCircle.Diameter
    If display.IsLinked Then currentLink = display.GetLinkedText
    If StrComp(currentLink, targetCircle.OriginalLinkedText, vbTextCompare) <> 0 Then Exit Function
    Set dimension = display.GetDimension2(0)
    If Abs(dimension.SystemValue - targetCircle.OldDimensionM) > TOLERANCE_M Then
        If dimension.SetSystemValue3(targetCircle.OldDimensionM, swSetValue_InThisConfiguration, Empty) <> swSetValue_Successful Then Exit Function
    End If
    RestoreOriginalValue = True
Failed:
End Function

Private Function OriginalDimensionsRestored(ByRef circles() As CircleInfo, ByVal count As Long) As Boolean
    Dim index As Long
    Dim display As SldWorks.DisplayDimension
    Dim dimension As SldWorks.Dimension
    For index = 1 To count
        If circles(index).Segment.GetRelationsCount <> circles(index).OriginalRelations Then Exit Function
        If circles(index).HadDiameter Then
            Set display = circles(index).Diameter
            If display.IsLinked Then
                If StrComp(display.GetLinkedText, circles(index).OriginalLinkedText, vbTextCompare) <> 0 Then Exit Function
            ElseIf Len(circles(index).OriginalLinkedText) > 0 Then
                Exit Function
            End If
            Set dimension = display.GetDimension2(0)
            If StrComp(dimension.Name, circles(index).OriginalDimensionName, vbBinaryCompare) <> 0 Then Exit Function
            If Abs(dimension.SystemValue - circles(index).OldDimensionM) > TOLERANCE_M Then Exit Function
        End If
    Next index
    OriginalDimensionsRestored = True
End Function

Private Function FormatSkipDetails(ByRef circles() As CircleInfo, ByVal count As Long) As String
    Dim counts(SkipOutsideGroup To SkipRelation) As Long
    Dim index As Long, reason As Long
    For index = 1 To count
        If circles(index).Candidate Then
            reason = circles(GroupRoot(circles, index)).Reason
            If reason <> SkipNone Then counts(reason) = counts(reason) + 1
        End If
    Next index
    For reason = SkipOutsideGroup To SkipRelation
        If counts(reason) > 0 Then
            FormatSkipDetails = FormatSkipDetails & vbCrLf & SkipLabel(reason) & " " & counts(reason)
        End If
    Next reason
End Function

Private Function SkipLabel(ByVal reason As Long) As String
    Select Case reason
        Case SkipOutsideGroup: SkipLabel = U(&H7B49, &H5F84, &H7EC4, &H5305, &H542B, &H8303, &H56F4, &H5916, &H5B9E, &H4F53)
        Case SkipExistingRadius: SkipLabel = U(&H5DF2, &H6709, &H534A, &H5F84, &H5C3A, &H5BF8)
        Case SkipLockedDimension: SkipLabel = U(&H73B0, &H6709, &H5C3A, &H5BF8, &H65E0, &H6CD5, &H72EC, &H7ACB, &H4FEE, &H6539)
        Case SkipConflictingLink: SkipLabel = U(&H73B0, &H6709, &H94FE, &H63A5, &H4E0E, &H672C, &H6B21, &H64CD, &H4F5C, &H51B2, &H7A81)
        Case SkipRelation: SkipLabel = U(&H73B0, &H6709, &H5173, &H7CFB, &H9650, &H5236, &H6539, &H5F84)
    End Select
End Function

Private Sub ShowSelection(ByVal model As SldWorks.ModelDoc2, ByRef circles() As CircleInfo, ByVal count As Long, ByVal strict As Boolean)
    Dim index As Long
    model.ClearSelection2 True
    For index = 1 To count
        If circles(index).Candidate Then
            If circles(index).Segment.Select4(True, Nothing) = False And strict Then Err.Raise BATCH_ERROR, , "Could not select a target circle."
        End If
    Next index
    model.GraphicsRedraw2
End Sub

Private Sub SetToggle(ByVal swApp As SldWorks.SldWorks, ByVal preference As Long, ByVal enabled As Boolean)
    If (swApp.GetUserPreferenceToggle(preference) <> False) <> enabled Then
        swApp.SetUserPreferenceToggle preference, enabled
        If (swApp.GetUserPreferenceToggle(preference) <> False) <> enabled Then Err.Raise BATCH_ERROR, , "Could not set a sketch preference."
    End If
End Sub

Private Function ReadPositiveNumber(ByVal text As String, ByRef value As Double) As Boolean
    On Error GoTo Invalid
    If Not IsNumeric(text) Then Exit Function
    value = CDbl(text)
    ReadPositiveNumber = value > 0#
Invalid:
End Function

Private Function DialogTitle() As String
    DialogTitle = U(&H6279, &H91CF, &H5706, &H76F4, &H5F84)
End Function

Private Function DiameterPrompt(ByVal selected As Long, ByVal eligible As Long) As String
    DiameterPrompt = U(&H5DF2, &H9009, &H4E2D) & " " & selected & " " & U(&H4E2A, &H672A, &H5B8C, &H5168, &H5B9A, &H4E49, &H7684, &H5706, &H3002)
    If selected <> eligible Then DiameterPrompt = DiameterPrompt & vbCrLf & U(&H53EF, &H5904, &H7406) & " " & eligible & " " & U(&H4E2A, &H3002)
    DiameterPrompt = DiameterPrompt & vbCrLf & U(&H8F93, &H5165, &H7EDF, &H4E00, &H76F4, &H5F84, &HFF08, &H6BEB, &H7C73, &HFF09, &H3002)
End Function

Private Function DescribeResult(ByVal result As String) As String
    Dim fields As Variant
    Select Case result
        Case "NO_SKETCH"
            DescribeResult = U(&H8BF7, &H5148, &H7F16, &H8F91, &H4E00, &H4E2A, &H4E8C, &H7EF4, &H8349, &H56FE, &H3002)
        Case "NO_TARGETS"
            DescribeResult = U(&H5F53, &H524D, &H8349, &H56FE, &H6CA1, &H6709, &H672A, &H5B8C, &H5168, &H5B9A, &H4E49, &H7684, &H5B8C, &H6574, &H5706, &H3002)
        Case "INVALID_INPUT"
            DescribeResult = U(&H8BF7, &H8F93, &H5165, &H5927, &H4E8E, &H96F6, &H7684, &H6570, &H503C, &H3002)
        Case "SKETCH_UNAVAILABLE"
            DescribeResult = U(&H8349, &H56FE, &H5F53, &H524D, &H65E0, &H6CD5, &H4FEE, &H6539, &H3002)
        Case "IN_CONTEXT_EDIT"
            DescribeResult = U(&H8BF7, &H5355, &H72EC, &H6253, &H5F00, &H7EC4, &H4EF6, &H540E, &H7F16, &H8F91, &H8349, &H56FE, &H3002)
        Case "MULTI_CONFIGURATION"
            DescribeResult = U(&H6B64, &H5B8F, &H9002, &H7528, &H4E8E, &H53EA, &H6709, &H4E00, &H4E2A, &H914D, &H7F6E, &H7684, &H6587, &H6863, &H3002)
        Case "UNDO_OK"
            DescribeResult = U(&H5DF2, &H8FD8, &H539F, &H4E0A, &H6B21, &H6279, &H91CF, &H6539, &H5F84, &H3002)
        Case "UNDO_NO_BATCH"
            DescribeResult = U(&H672C, &H6B21, &H5B8F, &H4F1A, &H8BDD, &H6CA1, &H6709, &H53EF, &H4EE5, &H8FD8, &H539F, &H7684, &H6279, &H6B21, &H3002)
        Case "UNDO_WRONG_SKETCH"
            DescribeResult = U(&H8BF7, &H4FDD, &H6301, &H4E0A, &H6B21, &H5904, &H7406, &H7684, &H4E8C, &H7EF4, &H8349, &H56FE, &H5904, &H4E8E, &H7F16, &H8F91, &H72B6, &H6001, &H3002)
        Case "UNDO_STALE"
            DescribeResult = U(&H8349, &H56FE, &H5F53, &H524D, &H72B6, &H6001, &H5DF2, &H53D8, &H5316, &HFF0C, &H65E0, &H6CD5, &H8FD8, &H539F, &H4E0A, &H6B21, &H6279, &H6B21, &H3002)
        Case Else
            If Left$(result, 3) = "OK " Then
                fields = Split(Mid$(result, 4), "|")
                DescribeResult = U(&H5DF2, &H5904, &H7406) & " " & fields(0) & " / " & fields(1) & " " & U(&H4E2A, &H5706, &H3002) & vbCrLf & U(&H5171, &H4EAB, &H76F4, &H5F84) & " = " & fields(3) & " mm" & vbCrLf & fields(4)
                DescribeResult = DescribeResult & fields(5)
                If UBound(fields) >= 6 Then DescribeResult = DescribeResult & vbCrLf & U(&H64A4, &H9500, &H8BB0, &H5F55, &H4E0D, &H53EF, &H7528, &H3002)
            ElseIf Left$(result, 12) = "NO_ELIGIBLE|" Then
                DescribeResult = U(&H6240, &H9009, &H5706, &H7684, &H5C3A, &H5BF8, &H6216, &H5173, &H7CFB, &H65E0, &H6CD5, &H5408, &H5E76, &H3002) & Mid$(result, 13)
            ElseIf Left$(result, 18) = "ERROR_ROLLED_BACK " Then
                DescribeResult = U(&H6279, &H91CF, &H6539, &H5F84, &H5931, &H8D25, &HFF0C, &H5DF2, &H64A4, &H9500, &H672C, &H6B21, &H66F4, &H6539, &H3002) & vbCrLf & Mid$(result, 19)
            ElseIf Left$(result, 17) = "ERROR_CHECK_UNDO " Then
                DescribeResult = U(&H6279, &H91CF, &H6539, &H5F84, &H5931, &H8D25, &HFF0C, &H8BF7, &H68C0, &H67E5, &H64A4, &H9500, &H8BB0, &H5F55, &H548C, &H8349, &H56FE, &H3002) & vbCrLf & Mid$(result, 18)
            Else
                DescribeResult = result
            End If
    End Select
End Function

Private Function U(ParamArray codes() As Variant) As String
    Dim code As Variant
    For Each code In codes
        U = U & ChrW(CLng(code))
    Next code
End Function
